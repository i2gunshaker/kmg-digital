# Готовые артефакты (deliverables/)

Пять пакетов, собранных конвейером `kurs` и проверенных перед заморозкой 26.11. Папка самодостаточна: курсы открываются с `file://` без сети, у роликов рядом лежат паспорт, субтитры и сцена Remotion. Тексты глав учебников (`.md`, `pages.json`, `pdf_pages.json`) сюда не входят.

## Состав

| Пакет | Что внутри | Параметры | Проверка |
| --- | --- | --- | --- |
| `courses/latyshova_gl5_par17-20/` | `course/index.html` + `course/assets/` (CSS, JS, KaTeX, шрифты, 161 SVG), `layer.json`, `coverage.json`, `tasks.json`, `routing.json` | 24 узла, 31 задание (5 типов), 3 шага отправлены в ролик | `qa_course.mjs`: 390×844 и 1280×800, без горизонтальной прокрутки, шрифт 16 px, офлайн |
| `courses/basniev_gl23-24/` | то же, 158 SVG | 21 узел, 27 заданий (5 типов), 6 шагов в ролик | то же |
| `courses/kuznetsov_gl10/` | то же, 132 SVG | 17 узлов, 23 задания (5 типов), 5 шагов в ролик | то же |
| `videos/latyshova_shorts_high_rt/` | `latyshova_shorts_high_rt.mp4`, `passport.json`, `subtitles.vtt`, `scene.json` | 9:16, 1080×1920, 30 к/с, 67,3 с; узел `latyshova.rho_kp_palette` | −16,08 LUFS, true peak −1,5 dBTP, WER 0,059, дрейф субтитров ≤ 0,20 с, `audio_check.status = ok` |
| `videos/basniev_deepdive_elastic_funnel/` | `basniev_deepdive_elastic_funnel.mp4`, `passport.json`, `subtitles.vtt`, `scene.json` | 16:9, 1920×1080, 30 к/с, 92,2 с; узел `basniev.elastic_regime_mechanism` | −16,04 LUFS, true peak −1,5 dBTP, WER 0,058, дрейф ≤ 0,32 с, `audio_check.status = ok` |

Озвучка роликов: ElevenLabs `eleven_v4`, голос Nikolay, `language_code=ru`. Распознавание для WER и дрейфа: Whisper small, локально. Паспорта проходят `passport.schema.json` из пакета вводных; поле `scene_spec` указывает на `scene.json` в этой же папке.

**Слой курсов.** Три курса собраны на прогоне Claude Haiku 5.5 26.11 около 02:30 (`generated_by` в `layer.json`). В этом прогоне 0 ошибок проверки (`check_errors`), покрытие единиц главы 100% (177/177, 394/394, 143/143). Все 16 рисунков, которые нарисовала модель (7 + 4 + 5), приняла проверка. Все рисунки вшиты в `course/assets/img/` в виде SVG. Файлы `domain/geology/visual/*.json` в репозитории сохраняют исходные ключи узлов слоя Opus/Sonnet из `08131a0`. Поэтому в чистом клоне без `build/` все тесты зелёные. `tests/test_course_and_specs.py::test_visual_specs_match_layer_nodes` падает только при локальном `build/<глава>/` со слоем Haiku.

## Как воспроизвести

```bash
# курс главы (слой, задания, маршруты, рисунки, страница)
python -m kurs run <глава> --max-usd 0.6
KURS_LLM_OFFLINE=1 python -m kurs rebuild <глава>          # пересборка без вызовов модели
node video/qa_course.mjs deliverables/courses/<глава>/course/index.html

# оба обучающих ролика (9:16 и 16:9) по паспортам из build/specs/
python -m kurs.video_render training --backend elevenlabs   # или --backend silero | edge_tts

# ролик по любому узлу любой главы
python -m kurs video <глава> --node <node_id> --format 9:16|16:9 --max-usd 0.05
```

## Почему эти два ролика

- **Два контрастных формата.** Шортс Латышовой 9:16 на 67 с отвечает на один вопрос («почему высокое сопротивление — ещё не нефть») для телефона. Разбор Басниева 16:9 на 92 с выводит механизм упругого режима и рост воронки депрессии по формулам главы.
- **Разные типы содержания.** Одна глава геофизическая, с палеткой ρп–kп, оцифрованной со скана. Другая — по подземной гидромеханике, с кривыми, посчитанными по формулам. Так видно, что сцены Remotion строятся из спецификаций рисунков, а не рисуются под конкретный ролик.
- **Универсальная генерация.** Это образцы, а не предел: тот же конвейер делает ролик по любому узлу слоя командой `kurs video <глава> --node <node_id>`. Сценарий проверяется по утверждениям узла, звук нормируется до −16 LUFS и проверяется распознаванием.
