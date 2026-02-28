"""One-shot script to ensure all JSON files in data/raw/ have the correct broker_type in metadata."""

import json
import os
import sys
from collections import OrderedDict


# Mapping: server value -> broker_type
SERVER_TO_BROKER_TYPE = {
    "VantageInternational-Demo": "mt5",
    "VantageInternational-Live 14": "mt5",
    "kraken_websocket": "kraken_spot",
}

RAW_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data", "raw")


def process_file(filepath: str) -> str:
    """Process a single JSON file, returns status string."""
    with open(filepath, "r", encoding="utf-8") as f:
        raw_text = f.read()

    data = json.loads(raw_text, object_pairs_hook=OrderedDict)
    metadata = data.get("metadata")
    if metadata is None:
        return "NO_METADATA"

    server = metadata.get("server")
    if server is None:
        return "NO_SERVER"

    broker_type = SERVER_TO_BROKER_TYPE.get(server)
    if broker_type is None:
        return f"UNKNOWN_SERVER:{server}"

    existing = metadata.get("broker_type")

    # Already correct — skip write
    if existing == broker_type:
        # Verify position: broker_type should come right after server
        keys = list(metadata.keys())
        server_idx = keys.index("server")
        bt_idx = keys.index("broker_type")
        if bt_idx == server_idx + 1:
            return "OK_ALREADY"

    # Insert or overwrite broker_type right after "server"
    new_metadata: OrderedDict = OrderedDict()
    for key, value in metadata.items():
        if key == "broker_type":
            continue  # remove old position, will re-insert after server
        new_metadata[key] = value
        if key == "server":
            new_metadata["broker_type"] = broker_type

    data["metadata"] = new_metadata

    new_text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(new_text)

    if existing is None:
        return f"ADDED:{broker_type}"
    elif existing != broker_type:
        return f"FIXED:{existing}->{broker_type}"
    else:
        return f"REORDERED:{broker_type}"


def main() -> None:
    raw_dir = os.path.normpath(RAW_DIR)
    if not os.path.isdir(raw_dir):
        print(f"Directory not found: {raw_dir}")
        sys.exit(1)

    json_files = sorted(f for f in os.listdir(raw_dir) if f.endswith(".json"))
    print(f"Found {len(json_files)} JSON files in {raw_dir}\n")

    stats: dict[str, int] = {}
    unknown_files: list[tuple[str, str]] = []

    for filename in json_files:
        filepath = os.path.join(raw_dir, filename)
        status = process_file(filepath)
        category = status.split(":")[0]
        stats[category] = stats.get(category, 0) + 1

        if category == "UNKNOWN_SERVER":
            unknown_files.append((filename, status.split(":", 1)[1]))

    # Summary
    print("=== Summary ===")
    for key, count in sorted(stats.items()):
        print(f"  {key}: {count}")
    print(f"  TOTAL: {len(json_files)}")

    if unknown_files:
        print(f"\n⚠ UNKNOWN SERVER — {len(unknown_files)} file(s) not processed:")
        for fname, server in unknown_files:
            print(f"  {fname}  (server: {server})")
        sys.exit(1)
    else:
        print("\nAll files processed successfully.")


if __name__ == "__main__":
    main()
