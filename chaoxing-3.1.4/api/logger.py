from loguru import logger
from tqdm import tqdm
import os
import sys

tqdm_stream = sys.stderr

def tqdm_sink(msg):
    tqdm.write(msg.rstrip(), file=tqdm_stream)
    tqdm_stream.flush()

logger.remove()
if os.environ.get("CHAOXING_HEADLESS") != "1":
    logger.add(tqdm_sink, colorize=True, enqueue=True)
logger.add(
    os.environ.get("CHAOXING_LOG_FILE", "chaoxing.log"),
    rotation="5 MB", retention=3,
    level="INFO" if os.environ.get("CHAOXING_HEADLESS") == "1" else "TRACE",
    enqueue=True, backtrace=False, diagnose=False,
)
