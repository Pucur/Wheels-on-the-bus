for f in music/*.mp3; do

    ascii="$(printf "%s" "$f" | iconv -f utf8 -t ascii//TRANSLIT 2>/dev/null)"

    new="$(echo "$ascii" | sed "s/[^A-Za-z0-9._ ()\[\]\-]//g")"

    new="${new//\'/}"

    if [[ "$new" != "$f" ]]; then
        echo "RENAMING:"
        echo "  $f"
        echo "  -> $new"
        mv -i -- "$f" "$new"
    fi
done
