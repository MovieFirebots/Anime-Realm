# Use an official Python runtime as a parent image
FROM python:3.10-slim

# Set the working directory in the container
WORKDIR /app

# Copy the requirements file into the container at /app
COPY requirements.txt .

# Install any needed packages specified in requirements.txt
# Use --no-cache-dir to reduce image size
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application code into the container at /app
COPY . .

# Make port 8080 available to the world outside this container
# Koyeb will use the PORT environment variable, which defaults to 8080 in config.py
# EXPOSE 8080 # Not strictly necessary for Koyeb as it handles port mapping

# Define environment variables (these will be overridden by Koyeb's settings)
ENV PYTHONUNBUFFERED=1
ENV PORT=8080 
# Add other ENV vars here if you want defaults, but Koyeb is preferred for sensitive data

# Run bot.py when the container launches
# Koyeb uses this as the run command.
CMD ["python", "bot.py"]
