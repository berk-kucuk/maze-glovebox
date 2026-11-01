#!/usr/bin/env python3
"""Maze Glovebox — open a suspicious file without letting it touch you."""
import os
import sys

# Started with `python3 -P`, which keeps the caller's directory off sys.path;
# the app's own directory is put back explicitly, and only that.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

if __name__ == "__main__":
    from mazeglovebox import cli
    if cli.wants_cli(sys.argv[1:]):
        sys.exit(cli.main(sys.argv[1:]))
    from mazeglovebox.gui.app import run
    sys.exit(run())
