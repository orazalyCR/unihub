"""Runs on every Vercel build. Copies the single source of truth for the
frontend (unihub/site) into public/, which Vercel serves straight from its
CDN. Keeps the Render/local deployment and the Vercel deployment sharing the
exact same HTML/CSS/JS with nothing to keep in sync by hand."""
import shutil
from pathlib import Path

root = Path(__file__).parent
src = root / "unihub" / "site"
dst = root / "public"

if dst.exists():
    shutil.rmtree(dst)
shutil.copytree(src, dst)
print(f"Copied {src} -> {dst} ({len(list(dst.rglob('*')))} files)")
