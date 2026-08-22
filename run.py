"""Full daily cycle: pull -> compute -> report.

Run:  .\.venv\Scripts\python.exe run.py [days_back]
"""
import sys

import pull
import engine
import report

if __name__ == "__main__":
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 120
    pull.main(days)
    print()
    engine.main()
    report.main()
