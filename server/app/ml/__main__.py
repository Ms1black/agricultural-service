"""python -m app.ml train — обучить прогноз высыхания на открытых датасетах и зарегистрировать."""

import sys

from ..config import Settings
from ..db import Database
from .train import default_report, open_data, summary, train


def main(argv: list[str]) -> int:
    if argv[:1] != ["train"]:
        print(__doc__)
        return 1
    settings = Settings.from_env()
    db = Database(settings.db_path)
    db.migrate()
    data, trained_on = open_data()
    print(f"Примеров: {len(data)}, рядов: {data['series'].nunique()}. Обучение…", flush=True)
    result = train(db, settings.models_path, data, trained_on, default_report())
    print(summary(result))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
