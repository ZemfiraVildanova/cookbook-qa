# Prompts that shaped the work

Verbatim, with the step number. The conversation with the agent was in Russian; each prompt
is preceded by a one-line English summary. The agent's standing instructions are in
`CLAUDE.md`, the skills and `docs/ai/agent-brief.md`.

## Step 1 — opening message

Summary: read the assignment and the brief, follow section 3 of the brief, summarise the
plan, list risks, write SPEC.md without committing; wait for "старт".

```text
Мы начинаем тестовое задание. Весь контекст лежит в соседней папке, вне репозитория:

- `../cookbook-qa-brief/assignment.pdf` — условие задания;
- `../cookbook-qa-brief/agent-brief.md` — принятые решения, план из 15 шагов, правила работы и проверенные данные по размещению.

Прочитай оба файла полностью. Правила работы, запреты и случаи, когда нужно остановиться, описаны в разделе 3 брифа — следуй им.

Коротко о главном:

1. Со мной общайся по-русски. Всё, что попадает в репозиторий, пишется по-английски.
2. Работай самостоятельно, шаг за шагом по разделу 7 брифа. Подтверждений плана и коммитов не жди, коммить и отправляй изменения сам.
3. После каждого шага давай короткий отчёт: что сделано, какие коммиты, прошли ли тесты, сколько времени ушло.
4. Останавливайся и жди меня только в пяти местах: перед первым коммитом и после шагов 4, 9, 11 и 15.
5. Коммиты только от моего имени, без строк Co-Authored-By и без упоминаний ИИ.

Что я разрешаю заранее, чтобы ты не останавливался на этом:

- отправлять коммиты в ветку `main` репозитория `ZemfiraVildanova/cookbook-qa` (обычный `git push`, без force);
- добавить в репозиторий сценарий GitHub Actions, который размещает сервис на Northflank в моей команде `zemfira-cookbook-qa`. Этот сценарий передаёт секреты `ANTHROPIC_API_KEY` и `OPENAI_API_KEY` из GitHub Secrets в группу секретов Northflank и использует `NORTHFLANK_API_TOKEN` для запуска шаблона. Так и задумано, это описано в разделе 8 брифа;
- отправлять образ в `ghcr.io/zemfiravildanova/cookbook-qa` из этого сценария;
- один раз скопировать файл `~/.config/cookbook-qa/env` в `.env` в репозитории, не выводя его содержимое.

Сейчас сделай три вещи и остановись:

- перескажи в пяти–семи пунктах, что мы строим и какие решения уже приняты;
- перечисли, что тебе неясно или кажется рискованным;
- напиши SPEC.md целиком, но не коммить.

Я прочитаю SPEC и напишу «старт». После этого сделай первый коммит и продолжай по плану.
```

## Step 1 — SPEC review, round 1

Summary: ingredient terms must exist in the corpus; add "with my kids" style edge cases;
time phrases before ingredient markers; veto terms as whole words with exceptions.

```text
Перед стартом внеси в SPEC две правки и покажи изменённые места.

R-10 и раздел 8: термин после маркера включения или исключения принимается как ингредиент, только если он встречается в словаре ингредиентов корпуса (токены из всех строк ингредиентов). Термин вне словаря в applied_constraints не попадает и фильтром не применяется; он остаётся на усмотрение модели по R-18. Добавь в edge cases: «What can I cook with my kids?», «dinner with friends», «I have no idea what to cook» — ограничений нет, отказа из-за фильтра нет; «no more than 30 minutes» — это время, а не исключение. Временные фразы разбираются раньше маркеров ингредиентов.
Раздел 2.3: вето-термины сопоставляются как целые слова с допуском множественного числа, а не как подстрока. Добавь константу VETO_EXCEPTIONS с фразами, которые вето не вызывают: coconut milk, almond milk, soy milk, oat milk, rice milk, coconut cream, cream of tartar, peanut butter, almond butter, cocoa butter, nut butter, vegetable stock, vegetable broth. Добавь тест-кейсы: eggplant, graham crackers, butternut squash не дают вето.

Обнови таблицу констант и матрицу трассировки. Больше ничего не меняй.
```

Plus: "Политика «X-free для аллергенов = safety» - оставь как есть" (keep the allergen policy).

## Step 1 — SPEC review, round 2

Summary: accept an exclusion candidate by the exclusion filter's own rule (R-15), inclusion
by R-14; add `INGREDIENT_GROUPS`; return the user's word in `applied_constraints`.

```text
Ещё одна правка в SPEC до старта.

Кандидат на исключение принимается, если он совпадает хотя бы с одной строкой ингредиентов корпуса по правилу самого фильтра исключения (R-15), а не по словарю токенов. Для включения остаётся проверка целым словом по R-14.
Добавь константу INGREDIENT_GROUPS: обобщающее слово раскрывается в список терминов и для включения, и для исключения. Минимум: nut → nut, almond, walnut, pecan, cashew, hazelnut, pistachio, peanut, macadamia; dairy → milk, butter, cheese, cream, yogurt, yoghurt, ghee; meat → мясные термины из VEGETARIAN_VETO_TERMS; fish и seafood → рыбные и морские термины оттуда же. Для dairy действуют VETO_EXCEPTIONS. В applied_constraints возвращается исходное слово пользователя.
Добавь edge case и тест: «dessert without nuts» исключает рецепт с almonds или walnuts, даже если слова «nut» в его ингредиентах нет.

Обнови таблицу констант и матрицу. Покажи изменённые места.
```

## Step 1 — SPEC review, round 3 and start

```text
Добавь в INGREDIENT_STOPWORDS слова one, way, time, clue, idea. Старт.
```
