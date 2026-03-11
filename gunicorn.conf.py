# Gunicorn configuration file
# This is automatically loaded by gunicorn if present in the root directly.
# It ensures the timeout is 120 seconds even if the Render dashboard UI 
# overrides the 'startCommand' in render.yaml.

timeout = 160 # Increase worker timeout to 160 seconds to allow for long Gemini OCR operations
workers = 1 # Reduced to 1 to save memory on 512MB RAM free tier
threads = 4 # Use threads for I/O bound tasks (safe for memory, good for concurrent I/O)

# Prevent memory leaks by recycling workers periodically
max_requests = 50 # Restart the worker after 50 requests to clear memory bloat
max_requests_jitter = 10 # Add random jitter to prevent restarts at exactly the same time
