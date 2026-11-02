"""Private embedded Python entry point; runtime paths come only from ._pth."""
import os
import sys
sys.dont_write_bytecode = True
os.environ["AWEAR_BUNDLED_RUNTIME"] = "1"
from desktop import run
run()
