#!/bin/bash
echo "=============================="
echo "   REPO HIGH-LEVEL OVERVIEW"
echo "=============================="

echo ""
echo "--- PROJECT STRUCTURE (2 levels deep) ---"
find . -maxdepth 2 \
  -not -path '*/.git/*' \
  -not -path '*/node_modules/*' \
  -not -path '*/__pycache__/*' \
  -not -path '*/.venv/*' \
  | sort

echo ""
echo "--- FILE TYPE BREAKDOWN ---"
find . -type f \
  -not -path '*/.git/*' \
  -not -path '*/node_modules/*' \
  -not -path '*/__pycache__/*' \
  | sed 's/.*\.//' | sort | uniq -c | sort -rn | head -20

echo ""
echo "--- LARGEST FILES ---"
find . -type f \
  -not -path '*/.git/*' \
  -not -path '*/node_modules/*' \
  -not -path '*/__pycache__/*' \
  | xargs wc -l 2>/dev/null | sort -rn | head -20

echo ""
echo "--- PYTHON ENTRY POINTS ---"
find . -name "main.py" -o -name "app.py" -o -name "run.py" -o -name "server.py" | grep -v __pycache__

echo ""
echo "--- KEY CONFIG FILES ---"
find . -maxdepth 3 \( -name "*.yml" -o -name "*.yaml" -o -name "*.env" -o -name "requirements*.txt" -o -name "Dockerfile" \) -not -path '*/.git/*' | sort

echo ""
echo "--- GIT SUMMARY ---"
echo "Branch: $(git branch --show-current)"
git log --oneline -5

echo ""
echo "--- DEPENDENCIES ---"
[ -f requirements.txt ] && cat requirements.txt
