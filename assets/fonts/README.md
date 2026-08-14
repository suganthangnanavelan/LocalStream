# Fonts

`ui/text.py` looks here first for `Inter-Regular.ttf` or `Roboto-Regular.ttf`
before falling back to system fonts (Segoe UI / Arial on Windows). Drop a
`.ttf` in this folder if you want LocalStream's UI text to look the same on
every machine regardless of what's installed system-wide. Not required to
run — on Windows, `segoeui.ttf` is present by default and will be found
automatically.
