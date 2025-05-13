import logging
from app import app

# Configure logging
logging.basicConfig(level=logging.DEBUG)

if __name__ == "__main__":
    # Run the Flask application
    # The 0.0.0.0 host makes the server accessible from any IP address
    app.run(host="0.0.0.0", port=5000, debug=True)