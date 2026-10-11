"""Create a consistent SQLite backup, even while UniHub is running."""
import sqlite3, datetime
from pathlib import Path
from contextlib import closing
ROOT=Path(__file__).resolve().parent
source=ROOT/'unihub.db'
if not source.exists():raise SystemExit('No database yet. Start UniHub first.')
folder=ROOT/'backups';folder.mkdir(exist_ok=True)
target=folder/('unihub-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f')+'.db')
with closing(sqlite3.connect(source)) as src, closing(sqlite3.connect(target)) as dst:src.backup(dst)
print(target)
