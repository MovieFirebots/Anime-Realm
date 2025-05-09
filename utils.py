import re
import secrets
import httpx # For making API calls to link shortener
from urllib.parse import urljoin
from config import MODIJI_API_KEY, MODIJI_API_URL, APP_BASE_URL, VERIFICATION_ENDPOINT

# --- File Metadata Parsing ---
# This is a VERY basic example. You'll need to adapt this extensively.
# Common patterns: [Source] Anime Name S01E01 [Quality][Language].mkv
# Or: Anime Name - 01 (Season 1) [1080p BluRay Sub].mp4
FILE_METADATA_REGEX = re.compile(
    r"^(.*?)(?:[Ss](\d+))?[EeXx]?(\d+)(?:.*?\[(\d{3,4}p)\].*?)?(?:.*?\[(SUB|DUB)\].*?)?$",
    re.IGNORECASE
)
# Alternative for just series name if above fails
SERIES_NAME_REGEX = re.compile(r"^(.*?)(?:[Ss]\d+)?(?:[EeXx]\d+)?", re.IGNORECASE)


def parse_filename(filename):
    """
    Parses filename to extract anime metadata.
    Returns a dictionary with: series_name, season, episode, quality, language
    """
    # Normalize: replace dots and underscores with spaces for easier parsing
    normalized_filename = filename.replace('.', ' ').replace('_', ' ')
    
    match = FILE_METADATA_REGEX.search(normalized_filename)
    data = {
        "series_name": None,
        "season": None,
        "episode": None,
        "quality": None,
        "language": None
    }

    if match:
        series_candidate = match.group(1).strip()
        # Try to clean up series name from bracketed source tags like [HorribleSubs]
        series_candidate = re.sub(r"^\[.*?\]\s*", "", series_candidate).strip()
        data["series_name"] = series_candidate if series_candidate else None

        if match.group(2): # Season
            data["season"] = int(match.group(2))
        if match.group(3): # Episode
            data["episode"] = int(match.group(3))
        if match.group(4): # Quality (e.g., 720p, 1080p)
            data["quality"] = match.group(4).lower()
        if match.group(5): # Language (SUB/DUB)
            data["language"] = match.group(5).upper()
    
    # Fallback for series name if the complex regex didn't catch it well
    if not data["series_name"]:
        series_match = SERIES_NAME_REGEX.search(normalized_filename)
        if series_match and series_match.group(1):
            series_candidate = series_match.group(1).strip()
            series_candidate = re.sub(r"^\[.*?\]\s*", "", series_candidate).strip()
            data["series_name"] = series_candidate if series_candidate else None
            
    # If series_name is still None, use the original filename (without extension) as a last resort
    if not data["series_name"] and filename:
        data["series_name"] = filename.rsplit('.', 1)[0].strip()


    # Basic quality/language detection from filename if not caught by regex
    fn_lower = filename.lower()
    if not data["quality"]:
        if "1080p" in fn_lower: data["quality"] = "1080p"
        elif "720p" in fn_lower: data["quality"] = "720p"
        elif "480p" in fn_lower: data["quality"] = "480p"
    
    if not data["language"]:
        if "sub" in fn_lower: data["language"] = "SUB"
        if "dub" in fn_lower: data["language"] = "DUB"
        if "dual audio" in fn_lower: data["language"] = "DUAL" # Or map to SUB/DUB as preferred

    return data


# --- Token Generation ---
def generate_verification_token():
    """Generates a secure random token for link shortener verification."""
    return secrets.token_urlsafe(24) # Generates a 32-char URL-safe string

# --- Link Shortener ---
async def shorten_link(target_url):
    """
    Shortens a link using ModijiURL API (or your chosen service).
    This is a placeholder. You need to implement the actual API call.
    """
    if not MODIJI_API_KEY or not MODIJI_API_URL:
        print("WARN: ModijiURL API Key or URL not configured. Shortening disabled.")
        return target_url # Return original URL if not configured

    payload = {
        "key": MODIJI_API_KEY,
        "url": target_url
    }
    # Example using httpx (async http client)
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(MODIJI_API_URL, json=payload) # Or data=payload, check API docs
            response.raise_for_status() # Raises HTTPError for bad responses (4XX or 5XX)
            result = response.json()
            # Assuming API returns something like: {"short_url": "http://modi.ji/xyz"}
            return result.get("short_url", target_url)
        except httpx.RequestError as e:
            print(f"Error calling ModijiURL API: {e}")
            return target_url # Fallback to original URL on error
        except Exception as e:
            print(f"Error processing ModijiURL response: {e}")
            return target_url

def get_verification_callback_url(verification_token):
    """Constructs the full callback URL for the link shortener."""
    if not APP_BASE_URL:
        raise ValueError("APP_BASE_URL is not configured in .env")
    base = APP_BASE_URL.rstrip('/')
    endpoint = VERIFICATION_ENDPOINT.lstrip('/')
    return f"{base}/{endpoint}?token={verification_token}"

# --- Pagination Helper ---
def format_bytes(size):
    # 2**10 = 1024
    power = 2**10
    n = 0
    power_labels = {0 : '', 1: 'KB', 2: 'MB', 3: 'GB', 4: 'TB'}
    while size > power:
        size /= power
        n += 1
    return f"{size:.2f} {power_labels[n]}"
