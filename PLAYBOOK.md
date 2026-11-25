# PLAYBOOK: день теста

Порядок действий на тесте 26.11 (10:00–14:00). Всё, что не требует модели, — бесплатно и повторяемо.

## 0. Подготовка (до 10:00)

```bash
git checkout avervill && git pull origin avervill
.venv/bin/pip install -r requirements.txt          # в т.ч. faster-whisper для проверки звука вне Mac
cd video && npm install && cd ..                    # Playwright: проверка курса и кадры роликов
export KURS_INPUT_DIR="<папка с материалами>"
.venv/bin/python -m kurs llm                        # ключ и модели, бесплатно
.venv/bin/python -m pytest -q                       # все тесты зелёные
```

`.env`: `ANTHROPIC_API_KEY`, `ELEVENLABS_API_KEY` (озвучка). Значения ключей не печатать.

## 1. Тестовые главы — одной командой

```bash
.venv/bin/python -m kurs cost path/to/test_chapter.md          # прогноз цены, бесплатно
.venv/bin/python -m kurs run path/to/test_chapter.md --max-usd 0.4
```

`run` делает всё: единицы → слой и покрытие → маршруты → задания → рисунки → проверку → курс → манифест → строку в `build/metrics.csv`.
Обрыв сети не теряет работу: повторный запуск доделывает только пропущенные запросы.

Проверить результат:

```bash
.venv/bin/python -m kurs check <id> --strict       # смысловые замечания как ошибки — посмотреть глазами
.venv/bin/python -m kurs fuzz <id>                 # доля пойманных внесённых ошибок, фон ложных тревог
node video/qa_course.mjs build/<id>/course/index.html
```

## 2. Правка факта человеком (≤ 30 чел.-мин на главу)

1. Исправить утверждение в `build/<id>/layer.json` (текст, цитату, страницу).
2. `kurs plan <id>` — что устарело: утверждения, задания, ролики, курс.
3. `kurs rebuild <id> [<node_id>] [--video]` — пересборка без модели (`KURS_LLM_OFFLINE=1`): сломанные задания заменяются заданиями по правилам того же шага, курс пересобирается и проходит приёмку, манифест обновляется.
4. Записать правку в `manual_edits.csv` и закоммитить: журнал правок должен совпадать с `git log`.

## 3. Ролики

```bash
.venv/bin/python -m kurs video <id> --dry                       # сценарий и паспорт: проверить глазами
.venv/bin/python -m kurs video <id>                             # шортс 9:16 и разбор 16:9 по маршрутизации
.venv/bin/python -m kurs video <id> --node <node_id> --format long   # ролик по назначенному узлу
```

Каждый ролик: `build/<id>/video/<video_id>/` — `<video_id>.mp4`, `passport.json`, `scene.json`, `audio_report.json` (LUFS, WER, сдвиг, длительность).
Отчёт о покрытии узлов шаблонами сцен — `build/<id>/scene_coverage.json`.
Пункты чек-листа физики, которые код не проверяет, помечены в паспорте — их подтверждает специалист при просмотре.

## 4. Модуль «Закладки»

```bash
# слепой прогон: код + смысловой аудит + кадры моделью со зрением + строгий второй проход
.venv/bin/python -m kurs.review <путь>/контрольный_пакет/package.json -o build/review/ctrl_blind_v1.json --max-usd 0.3
# только ПОСЛЕ сохранения файла:
python <путь>/score_review.py build/review/ctrl_blind_v1.json <спрятанный_путь>/key.json
```

- Ответ проверяется по `review_output.schema.json` (ищется рядом с пакетом и в `пакет_вводных/02_модуль_проверки/`); ошибки схемы печатаются, код выхода 1.
- На один `target` — одно замечание: доказанное кодом важнее модели, тяжёлое — лёгкого.
- Замечание модели остаётся, только если её фраза-доказательство дословно найдена в главе и скептический второй проход его подтвердил.
- Без `--max-usd` (или с `--no-llm`) работают только проверки кодом — это бесплатный нижний предел.

## 4а. Смежный домен (бурение)

```bash
.venv/bin/python -m kurs run path/to/drilling_chapter.md --domain drilling --max-usd 0.4
```

Пакет `domain/drilling/` собран по образцу `domain/geology/`. Время сборки пакета и проверки специалистом записать в MANIFEST.

## 5. Сдача (до 09:45 заморозки — для обучающей части; на тесте — по регламенту)

```bash
.venv/bin/python -m kurs metrics --manifest        # build/metrics.csv + таблица для MANIFEST.md
git tag -a freeze-<дата> -m "Заморозка: <что проверено>"
```
