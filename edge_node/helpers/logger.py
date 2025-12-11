import logging
from datetime import datetime

# --- logging ---
class NodeFormatter(logging.Formatter):
    def format(self, record):
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        location = getattr(record, 'location_name', "ORCHESTRATOR")
        return f"[{timestamp} - {location}] {record.getMessage()}"

def setup_logger(name="Orchestrator"):
    logger = logging.getLogger(name)
    handler = logging.StreamHandler()
    handler.setFormatter(NodeFormatter())
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    return logger

# create the base instance
logger = setup_logger()