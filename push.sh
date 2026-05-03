#!/bin/bash
if [ -z "$1" ]; then
  echo "Usage: ./push.sh \"your commit message\""
  exit 1
fi
git add .
git commit -m "$1"
git push origin HEAD:main
echo "✅ Pushed to GitHub"
