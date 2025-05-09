from pymongo import MongoClient, UpdateOne
from pymongo.errors import DuplicateKeyError
import datetime
from config import MONGO_URI, DATABASE_NAME, TOKEN_EXPIRY_DURATION

# Initialize MongoDB connection
client = MongoClient(MONGO_URI)
db = client[DATABASE_NAME]

# Collections
files_collection = db["files"]
users_collection = db["users"]
pending_verifications_collection = db["pending_verifications"] # For shortener tokens

# Create indexes for faster queries
files_collection.create_index("file_id", unique=True)
files_collection.create_index([("file_name_normalized", "text"), ("caption_normalized", "text")]) # For text search
files_collection.create_index("series_name")
files_collection.create_index("quality")
files_collection.create_index("language")
users_collection.create_index("user_id", unique=True)
pending_verifications_collection.create_index("verification_token", unique=True)
pending_verifications_collection.create_index("user_id")
pending_verifications_collection.create_index("expires_at", expireAfterSeconds=0) # Auto-delete expired tokens

# --- File Operations ---
async def add_file(file_data):
    """Adds a file to the database. Prevents duplicates based on file_id."""
    try:
        # Add normalized fields for case-insensitive search and better matching
        file_data["file_name_normalized"] = file_data.get("file_name", "").lower()
        file_data["caption_normalized"] = file_data.get("caption", "").lower()
        file_data["indexed_at"] = datetime.datetime.utcnow()
        files_collection.insert_one(file_data)
        return True
    except DuplicateKeyError:
        return False # File already exists

async def get_file_by_id(file_id):
    """Retrieves a file by its Telegram file_id."""
    return files_collection.find_one({"file_id": file_id})

async def find_files(query, filters=None, page=1, page_size=10):
    """Searches for files with text search and applies filters. Supports pagination."""
    search_criteria = {}
    if query:
        search_criteria["$text"] = {"$search": query.lower()} # Use text index

    if filters:
        for key, value in filters.items():
            if value: # Only add filter if a value is provided
                search_criteria[key] = value

    total_files = files_collection.count_documents(search_criteria)
    results = list(files_collection.find(search_criteria)
                   .skip((page - 1) * page_size)
                   .limit(page_size))
    return results, total_files

async def count_total_files():
    return files_collection.count_documents({})

async def get_distinct_values(field, query=None, current_filters=None):
    """Gets distinct values for a field, optionally filtered by a search query and other filters."""
    pipeline = []
    match_stage = {}

    if query:
        match_stage["$text"] = {"$search": query.lower()}

    if current_filters:
        for key, value in current_filters.items():
            if value and key != field: # Don't filter by the field we're getting distinct values for
                 match_stage[key] = value
    
    if match_stage:
        pipeline.append({"$match": match_stage})
        
    pipeline.append({"$match": {field: {"$ne": None, "$ne": ""}}}) # Ensure field exists and is not empty
    pipeline.append({"$group": {"_id": f"${field}"}})
    pipeline.append({"$sort": {"_id": 1}})
    
    distinct_results = files_collection.aggregate(pipeline)
    return [doc["_id"] for doc in distinct_results]


# --- User and Token Operations ---
async def get_or_create_user(user_id, username=None, first_name=None):
    """Gets a user or creates a new one if they don't exist."""
    user = users_collection.find_one_and_update(
        {"user_id": user_id},
        {"$setOnInsert": {"user_id": user_id, "username": username, "first_name": first_name, "tokens": 0, "joined_at": datetime.datetime.utcnow()}},
        upsert=True,
        return_document=True # pymongo.ReturnDocument.AFTER
    )
    return user

async def get_user_tokens(user_id):
    """Gets the token balance for a user."""
    user = await get_or_create_user(user_id)
    return user.get("tokens", 0)

async def update_user_tokens(user_id, amount_change):
    """Updates a user's token balance. Can be positive or negative."""
    users_collection.update_one({"user_id": user_id}, {"$inc": {"tokens": amount_change}})

async def add_pending_verification(user_id, verification_token):
    """Stores a pending verification token for a user."""
    expires_at = datetime.datetime.utcnow() + datetime.timedelta(seconds=TOKEN_EXPIRY_DURATION)
    pending_verifications_collection.insert_one({
        "user_id": user_id,
        "verification_token": verification_token,
        "created_at": datetime.datetime.utcnow(),
        "expires_at": expires_at
    })

async def get_pending_verification(verification_token):
    """Retrieves and removes a pending verification token if it exists and hasn't expired."""
    # MongoDB's TTL index will handle actual expiry deletion.
    # We still check here to avoid race conditions or processing already "logically" expired tokens.
    return pending_verifications_collection.find_one_and_delete({
        "verification_token": verification_token,
        "expires_at": {"$gt": datetime.datetime.utcnow()}
    })

async def count_total_users():
    return users_collection.count_documents({})

# --- Stats ---
async def get_db_stats():
    """Gets database statistics (size)."""
    return db.command("dbstats")
