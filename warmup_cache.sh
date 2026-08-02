#!/usr/bin/env bash
# Prednacte grafy do Redis cache, aby byly pro uzivatele vzdy k dispozici.
# Spousteni z cronu napr. co 10 minut:
#   */10 * * * * /tmp/dite_zaznamy_mliko/warmup_cache.sh
set -e
cd "$(dirname "$0")"

BASE="http://localhost:5000"
for path in /graf.png /graf-denni.png; do
    if curl -fsS --max-time 30 -o /dev/null -w "%{http_code} %{size_download}" "$BASE$path"; then
        echo " $path"
    else
        echo "CHYBA: $path" >&2
    fi
done
