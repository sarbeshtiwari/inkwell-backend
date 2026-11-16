"""Next.js App Router pages stream their data to the browser in self.__next_f.push([...]) chunks ("flight" data)."""
import json
import re


def flight_text(html):
    text = ""
    for chunk in re.findall(r"self\.__next_f\.push\((\[.*?\])\)</script>", html or "", re.S):
        try:
            c = json.loads(chunk)
        except ValueError:
            continue
        if len(c) > 1 and isinstance(c[1], str):
            text += c[1]
    return text


def flight_value(html, key):
    """The JSON value that follows "key": in the page's flight data, or None."""
    text = flight_text(html)
    m = re.search(rf'"{re.escape(key)}":\s*', text)
    if not m:
        return None
    try:
        return json.JSONDecoder().raw_decode(text[m.end():])[0]
    except ValueError:
        return None
