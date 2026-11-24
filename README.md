# Text Compare

An ad-free web tool to compare two texts side by side, built with Python (Flask).
It detects the format automatically and formats both sides before comparing, so only real differences are shown.

**Live Demo:** _add your Vercel URL here_

## Features
- Auto-detects **JSON, SOAP 1.1 / 1.2, XML, HTML, YAML, form data and plain text**
- Cleans escaped text from logs (`\"`, `\n`, stray quotes)
- Pretty-formats JSON and XML, including XML inside SOAP `CDATA` blocks
- Line and character-level highlighting with a similarity percentage
- Options: ignore whitespace, ignore case, sort JSON keys, only show differences
- Open files, swap sides, light and dark mode

## Run locally
```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
python app.py
```
Open http://127.0.0.1:5000

## Deploy
Push to GitHub and import the repo at [vercel.com/new](https://vercel.com/new). Vercel detects the Flask app automatically.

## Author
**Usman Farooq**