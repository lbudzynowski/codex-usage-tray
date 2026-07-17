#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

VERSION="$(
    python3 - <<'PY'
import ast
from pathlib import Path

path = Path("src/codex_usage_tray/__init__.py")
module = ast.parse(path.read_text(encoding="utf-8"))

for node in module.body:
    if not isinstance(node, ast.Assign):
        continue

    for target in node.targets:
        if isinstance(target, ast.Name) and target.id == "__version__":
            if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                print(node.value.value)
                raise SystemExit(0)

raise SystemExit("Unable to find a valid __version__ assignment")
PY
)"

PACKAGE_NAME="codex-usage-tray"
BUILD_ROOT="$(mktemp -d)"
PACKAGE_ROOT="$BUILD_ROOT/${PACKAGE_NAME}_${VERSION}"
OUTPUT_DIR="$PROJECT_ROOT/dist"
OUTPUT_FILE="$OUTPUT_DIR/${PACKAGE_NAME}_${VERSION}_all.deb"

cleanup() {
    rm -rf "$BUILD_ROOT"
}
trap cleanup EXIT

install -d \
    "$PACKAGE_ROOT/DEBIAN" \
    "$PACKAGE_ROOT/usr/bin" \
    "$PACKAGE_ROOT/usr/lib/python3/dist-packages/codex_usage_tray" \
    "$PACKAGE_ROOT/usr/share/applications" \
    "$PACKAGE_ROOT/usr/share/doc/$PACKAGE_NAME" \
    "$PACKAGE_ROOT/usr/share/man/man1" \
    "$PACKAGE_ROOT/etc/xdg/autostart" \
    "$OUTPUT_DIR"

sed "s/@VERSION@/$VERSION/g" \
    packaging/debian/control.in \
    > "$PACKAGE_ROOT/DEBIAN/control"

install -m 0644 \
    packaging/debian/conffiles \
    "$PACKAGE_ROOT/DEBIAN/conffiles"

while IFS= read -r -d '' source_file; do
    filename="$(basename "$source_file")"
    install -m 0644 \
        "$source_file" \
        "$PACKAGE_ROOT/usr/lib/python3/dist-packages/codex_usage_tray/$filename"
done < <(find src/codex_usage_tray -maxdepth 1 -type f -name '*.py' -print0)

install -m 0755 \
    packaging/debian/codex-usage-tray \
    "$PACKAGE_ROOT/usr/bin/codex-usage-tray"

install -m 0644 \
    packaging/debian/codex-usage-tray.desktop \
    "$PACKAGE_ROOT/usr/share/applications/codex-usage-tray.desktop"

install -m 0644 \
    packaging/debian/codex-usage-tray-autostart.desktop \
    "$PACKAGE_ROOT/etc/xdg/autostart/codex-usage-tray.desktop"

install -m 0644 \
    README.md \
    "$PACKAGE_ROOT/usr/share/doc/$PACKAGE_NAME/README.md"

install -m 0644 \
    LICENSE \
    "$PACKAGE_ROOT/usr/share/doc/$PACKAGE_NAME/copyright"

gzip -9n -c \
    packaging/debian/changelog \
    > "$PACKAGE_ROOT/usr/share/doc/$PACKAGE_NAME/changelog.gz"

gzip -9n -c \
    packaging/debian/codex-usage-tray.1 \
    > "$PACKAGE_ROOT/usr/share/man/man1/codex-usage-tray.1.gz"

chmod 0644 \
    "$PACKAGE_ROOT/usr/share/doc/$PACKAGE_NAME/changelog.gz" \
    "$PACKAGE_ROOT/usr/share/man/man1/codex-usage-tray.1.gz"

rm -f "$OUTPUT_FILE"

dpkg-deb --root-owner-group --build \
    "$PACKAGE_ROOT" \
    "$OUTPUT_FILE"

printf 'Built %s\n' "$OUTPUT_FILE"
