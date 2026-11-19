from pathlib import Path


def create_unique_dir(base_dir: Path) -> Path:
    index = 0
    candidate = base_dir
    while True:
        try:
            candidate.mkdir(parents=True)
            return candidate
        except FileExistsError:
            index += 1
            candidate = base_dir.with_name(f"{base_dir.name}_{index}")
