# Astro Modeler — установка и проверка

Astro Modeler предоставляет одной явно подключённой Blender session четыре public MCP tools: `create_cube`, `get_selected_context`, `create_box_at_cursor`, `post_modeling_note`. Проверенная среда — Windows, Blender 5.0.1 с Python 3.11.13, внешний Python 3.13.8 и Codex CLI. Другие версии и ОС не проверены. Архитектура описана в `Documentation/04_Архитектура.md`.

## Подготовка из корня репозитория

Команды ниже выполняются в PowerShell из каталога клона. `python` должен указывать на внешний Python 3.13; Python Blender отдельно не изменяется.

```powershell
python --version
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r MCP/requirements.txt
.\.venv\Scripts\python.exe Plugins/AstroModeler/package_addon.py
```

MCP использует официальный Python SDK `mcp==1.29.1` и его FastMCP v1 API. Прямая зависимость закреплена; транзитивные версии разрешает pip, полного lockfile в прототипе нет. Add-on использует только Blender `bpy` и стандартную библиотеку, установка pip-пакетов внутрь Blender не нужна. Сведения об SDK: [официальный Python SDK MCP](https://github.com/modelcontextprotocol/python-sdk/tree/v1.x).

## Установка в Blender

1. Собери `dist/astro_modeler-0.1.0.zip` предыдущей командой.
2. В Blender открой Edit → Preferences → Add-ons → меню → Install from Disk и выбери этот ZIP. Это обычный legacy add-on с `bl_info`; архив содержит пакет `astro_modeler/`.
3. Включи Astro Modeler. В 3D View открой Sidebar клавишей N → Astro Modeler.
4. В нужной сцене выбери Object Mode и нажми **Start Integration**. Статус: `Listening on localhost`.

После обновления Python-кода установленного add-on Blender может продолжать использовать уже загруженную старую версию модуля. Если поведение не соответствует свежему source или ZIP, полностью перезапусти Blender до вывода, что новый код не работает.

Порядок установки ZIP описан в [руководстве Blender по Add-ons](https://docs.blender.org/manual/en/4.4/editors/preferences/addons.html). Сам ZIP дополнительно проверен через операторы install/enable в Blender 5.0.1 с изолированным пользовательским каталогом.

Интеграция включается явно и принимает команды только в этой сессии. Другая сессия не сможет занять тот же `127.0.0.1:55881`. Если порт занят, останови Astro Modeler в предыдущей сессии; случайный свободный порт автоматически не выбирается. После загрузки другого `.blend` снова нажми Start Integration. Кнопка Stop Integration и отключение add-on освобождают соединение.

## Подключение Codex

Из корня репозитория выполни один раз:

```powershell
$astroPython = (Resolve-Path '.venv/Scripts/python.exe').Path
$astroServer = (Resolve-Path 'MCP/server.py').Path
codex mcp add astro_modeler -- $astroPython $astroServer
codex mcp get astro_modeler
```

Эта команда записывает MCP server в пользовательскую конфигурацию Codex. Она приведена для установки пользователем; агент в TASK 003 сохранённую конфигурацию Codex не менял. Пути вычисляются из текущего клона, персональный путь не является частью шаблона.

Codex запускает MCP server как STDIO subprocess сам; запускать `MCP/server.py` вручную в отдельном терминале не нужно. После добавления перезапусти Codex app для перечитывания MCP settings. Настройка STDIO и команда `codex mcp add` подтверждены [официальной документацией Codex MCP](https://developers.openai.com/codex/mcp).

Подтверждённый runtime: Astro Modeler зарегистрирован как внешний STDIO MCP Codex; GPT-5.6 Sol видит все четыре tools. Sol MEDIUM прошёл моделирующие и Agent Feedback вызовы, Sol LOW ранее успешно выполнял простые tool-вызовы. Это не делает LOW достаточным для любой будущей TASK: модель и reasoning выбираются по сложности, а текущая рабочая практика для ближайших технических TASK — GPT-5.6 Sol MEDIUM.

В диалоге Codex должны быть доступны четыре инструмента:

- `create_cube()` — новый куб 2 × 2 × 2 в мировом начале координат;
- `get_selected_context()` — компактный read-only контекст selection, active object и 3D Cursor без mesh data;
- `create_box_at_cursor(size_x, size_y, size_z)` — world-aligned Box в позиции Cursor;
- `post_modeling_note(status, summary, details="")` — сообщение в `AGENT FEEDBACK LOG` Blender.

Пример результата `create_cube`:

```json
{"success": true, "object_name": "Cube.001", "message": "Cube created in the current scene."}
```

Операция добавляет отдельный куб размером 2 × 2 × 2 единицы Blender в мировом начале координат и делает его активным. Повторный вызов добавляет ещё один куб в том же месте: объекты могут визуально перекрываться, поэтому проверяй имя в Outliner. Имя назначает Blender; оно зависит от существующих объектов. Undo/transactions этим прототипом не гарантируются.

`AGENT FEEDBACK LOG` находится в Sidebar / N-panel вкладки Astro Modeler. Он показывает новые записи сверху, различает `OK`, `WARNING`, `BLOCKED`, выводит диапазон времени, repeat count, полный summary и компактный preview непустых details; полный details раскрывается локальной кнопкой. Состояние раскрытия служебное и не попадает в Scene/`.blend`. До 20 последовательных clusters хранятся newest-first. Точно одинаковые соседние notes по `status + summary + details` объединяются; после другой note тот же текст начинает новый cluster. `Clear Feedback` очищает только runtime feedback; Start Integration, загрузка другого `.blend` и disable/unregister также начинают с пустого списка. Feedback не хранится в `.blend`, Scene/Object properties или Text datablocks.

После подтверждённой доставки note MCP-клиент обновляет компактную JSONL-историю `<repo>/.runtime/agent_feedback.jsonl`. Cluster содержит только `first_time`, `last_time`, `repeat_count`, `status`, `summary`, `details`; последовательные точные дубли агрегируются так же, как в UI. Файл удерживает последние 200 clusters; session token и Selected Context туда не попадают. Это вторичная локальная диагностика. Новая TASK не читает файл автоматически: для конкретного расследования используй небольшой tail, диапазон или фильтр, не весь log.

## Ошибки и границы

- `No connected Blender session` — включи add-on и Start Integration. Обычный запуск MCP и Blender должен происходить от одного пользователя с одним временным каталогом ОС.
- Ошибка Object Mode — выйди из Edit/Sculpt Mode перед вызовом.
- Ошибка подключения или timeout — сначала проверь сцену. Если ответ потерян, операция могла успеть выполниться; автоматического повтора нет. При необходимости переподключи Astro Modeler.
- Закрытие/сбой Blender может оставить устаревший session descriptor. Новый успешный Start Integration заменяет его. После остановки MCP Blender остаётся подключённым до Stop Integration, загрузки файла или завершения Blender.

Служебный descriptor находится в `tempfile.gettempdir()/astro_modeler/session.json`, содержит localhost endpoint и случайный token текущего запуска. Не копируй его в документацию или Git. Это локальный прототип для одного пользователя; multi-user security, удалённый доступ, multi-session routing и production hardening не реализованы.

## Выполненные проверки и воспроизведение

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s Tests -p 'test_*.py' -v
.\.venv\Scripts\python.exe Tests/smoke_mcp.py
```

Socket/protocol tests проверяют ограничения входа, ошибки, подтверждённую доставку feedback и bounded JSONL. SDK smoke проверяет реальный STDIO handshake, discovery четырёх tools и ошибки отсутствующей сессии.

Для теста через установленный Codex сначала останови обычную интеграцию. В отдельном терминале запусти Blender с `--factory-startup --python Tests/blender_runtime.py`, указав фактический путь к Blender executable и абсолютный путь к скрипту. Затем из корня проекта:

```powershell
.\.venv\Scripts\python.exe Tests/codex_runtime.py
```

Скрипт использует экспериментальный протокол установленного Codex app-server: обнаруживает tool и выполняет ровно два вызова. Он создаёт только временный protocol context без модельного turn и не меняет сохранённую MCP-конфигурацию. API app-server может измениться между версиями Codex.

Реальный результат TASK 003: `Cube.001` и `Cube.002` в открытом GUI Blender (`background=false`), по 8 вершин и 6 граней, размеры 2 × 2 × 2; ответы Codex совпали со сценой. Blender отвечал после второго вызова. Данные сохранены в `.runtime/codex-result.json`, `.runtime/blender-state.json`, `.runtime/astro-modeler-smoke.blend` и независимо проверены повторным чтением `.blend`.

Для проверки ZIP, stop/restart/disable и отключения при загрузке файла закрой только тестовый Blender, затем выполни в отдельном PowerShell (переменные окружения действуют лишь в нём):

```powershell
$env:BLENDER_USER_SCRIPTS = Join-Path (Get-Location) '.runtime/blender-user/scripts'
$env:BLENDER_USER_CONFIG = Join-Path (Get-Location) '.runtime/blender-user/config'
$env:BLENDER_USER_EXTENSIONS = Join-Path (Get-Location) '.runtime/blender-user/extensions'
New-Item -ItemType Directory -Force -Path $env:BLENDER_USER_SCRIPTS,$env:BLENDER_USER_CONFIG,$env:BLENDER_USER_EXTENSIONS | Out-Null
# Подставь путь установленного Blender:
& '<BLENDER_EXECUTABLE>' --background --factory-startup --python-exit-code 1 --python Tests/blender_install.py
```

Это изолированная проверка установки; она не сохраняет пользовательские preferences. Первоначально проверялась цепочка через MCP-клиент установленного Codex app-server и реальный открытый Blender, без имитации `bpy`. После этого пользователь установил Astro Modeler в рабочий Blender, нажал Start Integration и подтвердил создание `Cube` по команде из обычного текстового Codex. TASK 003 принята пользователем; итог синхронизирован в `Documentation/20_SplitDoc/TASK_HISTORY.md`. Постоянная конфигурация MCP агентом не изменялась.

`.venv/`, `.runtime/`, `dist/` и Python caches исключены из Git. Runtime-скрипты служат только тестам; они не добавляют публичные инструменты чтения сцены, Python execution или screenshots.
