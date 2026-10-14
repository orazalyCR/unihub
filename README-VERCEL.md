# UniHub на Vercel

Второй, независимый способ запуска сайта — рядом с Render/локальным `unihub/server.py`,
ничего в нём не меняя. Бэкенд здесь — Flask (`app.py`) + Postgres вместо файла SQLite,
потому что у Vercel нет диска и постоянного процесса. Бизнес-логика (`vercel_features.py`)
— построчная копия `unihub/features.py`, адаптированная только там, где это требует
Postgres. Статика (`site/*.html`, `app.js`, `styles.css`) копируется в `public/` на каждой
сборке из `unihub/site` — то есть это ровно тот же интерфейс, что и на Render, обновлять
нужно только одно место.

Я прогнал бизнес-логику (регистрацию, вход, лайки, подписки, сообщения, маркетплейс,
rate-limit, права владельца) через 31 автоматическую проверку локально — все прошли.
То, что нельзя было проверить без вашей реальной базы — перевод SQL под Postgres — проверено
отдельно юнит-тестами и `verify_postgres.py` ниже.

## 1. Создайте бесплатную базу на Neon (2 минуты, без карты)

1. Зайдите на [neon.tech](https://neon.tech), зарегистрируйтесь (можно через GitHub).
2. Создайте проект. Любой регион.
3. На странице проекта нажмите **Connect** → выберите **Pooled connection** (это важно,
   не Direct) → скопируйте строку вида:
   ```
   postgresql://user:password@ep-xxx-pooler.region.aws.neon.tech/neondb?sslmode=require
   ```

## 2. Проверьте подключение локально, до деплоя

```bash
cd unihub-master
pip install -r requirements.txt
```

PowerShell:
```powershell
$env:DATABASE_URL="postgresql://...pooler..."
python verify_postgres.py
```

Должно напечатать 5 шагов с `OK` и в конце "All checks passed". Если что-то не так —
сюда же упадёт понятная ошибка (чаще всего — скопирована Direct-строка вместо Pooled,
или забыт `?sslmode=require`).

## 3. Выложите код на GitHub

Если репозиторий ещё не инициализирован:
```bash
git init
git add .
git commit -m "Add Vercel deployment (Flask + Postgres)"
git branch -M main
git remote add origin https://github.com/<ваш-аккаунт>/unihub.git
git push -u origin main
```

## 4. Подключите проект в Vercel

1. [vercel.com](https://vercel.com) → Sign up (через GitHub — проще всего).
2. **Add New** → **Project** → выберите этот репозиторий.
3. Vercel сам распознает Flask (`app.py` + `requirements.txt`) — ничего настраивать
   руками не нужно, **Root Directory** оставьте пустым (корень репозитория).
4. В **Environment Variables** добавьте:
   - `DATABASE_URL` = та же pooled-строка из шага 1.
5. **Deploy**.

Сборка на Vercel сама запустит `build_static.py` (прописано в `pyproject.toml`) и
скопирует `unihub/site` → `public/`, затем применит схему к базе при первом запросе.

## 5. Проверка после деплоя

Откройте `https://<ваш-проект>.vercel.app/api/health` — должно вернуть JSON
`{"app": "UniHub", ...}`. Затем откройте сам сайт и зарегистрируйтесь.

## Что здесь иначе, чем на Render, и почему это нормально

- **Таймауты.** На бесплатном плане Vercel функция обязана ответить за 10 секунд —
  для обычных запросов (вход, лента, лайк) этого с большим запасом хватает.
- **Rate-limit на вход** (5 неверных попыток / 5 минут) хранится в памяти процесса.
  Vercel может поднять несколько параллельных инстансов под нагрузкой — тогда лимит
  считается независимо на каждом. Для учебного проекта с небольшим трафиком это не
  критично; просто имейте в виду, что это не железная гарантия, как в single-process
  варианте на Render.
- **"Холодный старт".** Если какое-то время не было запросов, первый запрос после паузы
  займёт на секунду-две больше (и БД на Neon, и функция на Vercel просыпаются) — зато,
  в отличие от Render free, **данные при этом никуда не пропадают**: Neon — это
  настоящая постоянная база.

## Файлы этого деплоя

- `app.py` — Flask-версия `unihub/server.py` (роуты, сессии, куки).
- `vercel_features.py` — копия `unihub/features.py` (без изменений в логике).
- `dbshim.py` — прослойка, которая даёт `app.py`/`vercel_features.py` обращаться к
  Postgres точно так же, как раньше к `sqlite3` (`?`-плейсхолдеры, `row["col"]`,
  `cur.lastrowid`, `with connect() as c:` и т.д.) — бизнес-код из-за этого почти не
  пришлось переписывать.
- `schema_postgres.sql` — то же самое устройство базы, что в `unihub/server.py` +
  `unihub/features.py`, переведённое на синтаксис Postgres.
- `build_static.py` — копирует `unihub/site` → `public/` при каждой сборке.
- `verify_postgres.py` — одноразовая проверка перед первым деплоем (шаг 2 выше).
