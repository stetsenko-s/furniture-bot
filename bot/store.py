"""SQLite принадлежит боту. Сервисы не читают эту базу."""
import json
import sqlite3
import shutil
import uuid
from contextlib import contextmanager
from pathlib import Path

from contracts.models import Brief
from .readiness import pending_questions
from .workflow_store import init_schema


class Store:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.db = self.directory / "projects.sqlite3"
        with self.connect() as con:
            con.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS projects(
                    id TEXT PRIMARY KEY, user_id INTEGER NOT NULL,
                    title TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP);
                CREATE TABLE IF NOT EXISTS revisions(
                    project_id TEXT NOT NULL, number INTEGER NOT NULL,
                    brief TEXT NOT NULL, questions TEXT NOT NULL DEFAULT '[]',
                    analysed INTEGER NOT NULL DEFAULT 0,
                    approved INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY(project_id, number));
                CREATE TABLE IF NOT EXISTS messages(
                    id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT NOT NULL,
                    revision INTEGER NOT NULL, role TEXT NOT NULL, text TEXT NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP);
                CREATE TABLE IF NOT EXISTS attachments(
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL,
                    revision INTEGER NOT NULL, path TEXT NOT NULL, mime TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS artifacts(
                    project_id TEXT NOT NULL, revision INTEGER NOT NULL,
                    path TEXT NOT NULL, model TEXT NOT NULL,
                    PRIMARY KEY(project_id, revision));
                CREATE TABLE IF NOT EXISTS sessions(
                    user_id INTEGER PRIMARY KEY, project_id TEXT,
                    screen TEXT NOT NULL DEFAULT 'menu');
                CREATE TABLE IF NOT EXISTS controls(
                    user_id INTEGER PRIMARY KEY, chat_id INTEGER NOT NULL,
                    message_id INTEGER NOT NULL, media INTEGER NOT NULL DEFAULT 0,
                    payload TEXT NOT NULL DEFAULT '{}');
            """)
            init_schema(con)
            columns = {row['name'] for row in con.execute('PRAGMA table_info(projects)')}
            if 'number' not in columns:
                con.execute('ALTER TABLE projects ADD COLUMN number INTEGER')
            columns = {row['name'] for row in con.execute('PRAGMA table_info(revisions)')}
            for column in ('analysis_error', 'sketch_error'):
                if column not in columns:
                    con.execute(f'ALTER TABLE revisions ADD COLUMN {column} TEXT')
            columns = {row['name'] for row in con.execute('PRAGMA table_info(artifacts)')}
            if 'sketch_number' not in columns:
                con.execute('ALTER TABLE artifacts ADD COLUMN sketch_number INTEGER')
            # Номер изображения независим от правок описания. Старые результаты
            # получают номера по порядку сохранения, затем номера не меняются.
            for row in con.execute('SELECT project_id,revision FROM artifacts '
                                   'WHERE sketch_number IS NULL ORDER BY rowid').fetchall():
                number = con.execute('SELECT COALESCE(MAX(sketch_number),0)+1 FROM artifacts '
                                     'WHERE project_id=?', (row['project_id'],)).fetchone()[0]
                con.execute('UPDATE artifacts SET sketch_number=? WHERE project_id=? AND revision=?',
                            (number, row['project_id'], row['revision']))
            con.execute('CREATE UNIQUE INDEX IF NOT EXISTS artifact_sketch_number '
                        'ON artifacts(project_id,sketch_number)')
            # Однократная миграция существующих проектов, без сброса их данных.
            for row in con.execute('SELECT id,user_id,title FROM projects WHERE number IS NULL ORDER BY rowid').fetchall():
                number = con.execute('SELECT COALESCE(MAX(number),0)+1 FROM projects WHERE user_id=?',
                                     (row['user_id'],)).fetchone()[0]
                title = f'Проект №{number}' if row['title'] == 'Новый проект' else row['title']
                con.execute('UPDATE projects SET number=?,title=? WHERE id=?', (number, title, row['id']))

    @contextmanager
    def connect(self):
        con = sqlite3.connect(self.db, timeout=15)
        con.row_factory = sqlite3.Row
        try:
            with con:
                yield con
        finally:
            con.close()

    def project(self, user_id, project_id):
        with self.connect() as con:
            row = con.execute("SELECT * FROM projects WHERE id=? AND user_id=?",
                              (project_id, user_id)).fetchone()
        if row is None:
            raise LookupError("Проект не найден.")
        return dict(row)

    def projects(self, user_id):
        with self.connect() as con:
            return [dict(row) for row in con.execute(
                "SELECT * FROM projects WHERE user_id=? ORDER BY rowid DESC LIMIT 20",
                (user_id,))]

    def session(self, user_id):
        with self.connect() as con:
            row = con.execute("SELECT * FROM sessions WHERE user_id=?", (user_id,)).fetchone()
        return dict(row) if row else {"user_id": user_id, "project_id": None, "screen": "menu"}

    def navigate(self, user_id, screen, project_id=None):
        if project_id:
            self.project(user_id, project_id)
        else:
            project_id = self.session(user_id)["project_id"]
        with self.connect() as con:
            con.execute("""INSERT INTO sessions(user_id, project_id, screen) VALUES(?,?,?)
                ON CONFLICT(user_id) DO UPDATE SET project_id=excluded.project_id,
                screen=excluded.screen""", (user_id, project_id, screen))

    def create(self, user_id):
        project_id = uuid.uuid4().hex
        with self.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            # Номер зависит только от оставшихся проектов пользователя,
            # включая переименованные, а не от исторического счётчика.
            number = con.execute('SELECT COALESCE(MAX(number),0)+1 FROM projects WHERE user_id=?',
                                 (user_id,)).fetchone()[0]
            title = f'Проект №{number}'
            con.execute("INSERT INTO projects(id,user_id,title,number) VALUES(?,?,?,?)",
                        (project_id, user_id, title, number))
            brief = Brief.empty().model_copy(update={'title': title})
            con.execute("INSERT INTO revisions(project_id,number,brief) VALUES(?,?,?)",
                        (project_id, 1, brief.model_dump_json()))
        self.navigate(user_id, "intake", project_id)
        return self.project(user_id, project_id)

    def revision(self, user_id, project_id, number=None):
        project = self.project(user_id, project_id)
        number = project["revision"] if number is None else number
        with self.connect() as con:
            row = con.execute("SELECT * FROM revisions WHERE project_id=? AND number=?",
                              (project_id, number)).fetchone()
        if row is None:
            raise LookupError("Версия не найдена.")
        result = dict(row)
        result["brief"] = Brief.model_validate_json(result["brief"])
        result['brief'].title = project['title']
        result["questions"] = json.loads(result["questions"])
        return result

    def add_input(self, user_id, project_id, text, image=None, mime=None):
        project = self.project(user_id, project_id)
        old = self.revision(user_id, project_id)
        number = project["revision"] + 1
        # Файл сначала сохраняется под случайным именем, исходное имя не используется.
        attachment = None
        if image is not None:
            aid = uuid.uuid4().hex
            path = self.directory / "uploads" / project_id / (aid + ".image")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(image)
            attachment = (aid, project_id, number, str(path), mime)
        with self.connect() as con:
            con.execute("INSERT INTO revisions(project_id,number,brief) VALUES(?,?,?)",
                        (project_id, number, old["brief"].model_dump_json()))
            con.execute("UPDATE projects SET revision=? WHERE id=?", (number, project_id))
            con.execute("INSERT INTO messages(project_id,revision,role,text) VALUES(?,?,?,?)",
                        (project_id, number, "user", text))
            if attachment:
                con.execute("INSERT INTO attachments VALUES(?,?,?,?,?)", attachment)
        with self.connect() as con:
            con.execute("UPDATE workflow_state SET stage='sketch',version=NULL,approved_sketch=NULL WHERE project_id=?", (project_id,))
        self.navigate(user_id, "intake", project_id)
        return number

    def generation_brief(self, user_id, project_id, number):
        # Переименование интерфейса не меняет payload уже утверждённой версии:
        # повтор запроса должен совпадать с кэшем независимого генератора.
        self.revision(user_id, project_id, number)
        with self.connect() as con:
            row = con.execute('SELECT brief FROM revisions WHERE project_id=? AND number=?', (project_id, number)).fetchone()
        return Brief.model_validate_json(row['brief'])

    def save_analysis(self, user_id, project_id, number, result):
        project = self.project(user_id, project_id)
        if project["revision"] != number:
            raise ValueError("Версия уже изменилась.")
        brief = result.brief.model_copy(update={'title': project['title']})
        questions = list(result.questions)
        # Сохраняем вопросы в контексте: иначе ответы «1. да, 2. нет»
        # следующая модель не сможет надёжно связать с предыдущим экраном.
        displayed = pending_questions({'brief': brief, 'questions': questions},
                                      bool(self.attachments(user_id, project_id)))[:3]
        reply = result.reply
        if displayed:
            reply += '\nПеред созданием эскиза нужно уточнить:\n' + '\n'.join(
                f'{i}. {q}' for i, q in enumerate(displayed, 1))
        with self.connect() as con:
            con.execute("""UPDATE revisions SET brief=?,questions=?,analysed=1,analysis_error=NULL
                WHERE project_id=? AND number=?""",
                        (brief.model_dump_json(), json.dumps(questions, ensure_ascii=False),
                         project_id, number))
            con.execute("INSERT INTO messages(project_id,revision,role,text) VALUES(?,?,?,?)",
                        (project_id, number, "assistant", reply))

    def messages(self, user_id, project_id):
        self.project(user_id, project_id)
        with self.connect() as con:
            return [dict(row) for row in con.execute(
                "SELECT role,text,revision FROM messages WHERE project_id=? ORDER BY id",
                (project_id,))]

    def attachments(self, user_id, project_id, number=None):
        project = self.project(user_id, project_id)
        number = project["revision"] if number is None else number
        with self.connect() as con:
            return [dict(row) for row in con.execute(
                """SELECT * FROM attachments WHERE project_id=? AND revision<=?
                ORDER BY revision DESC,rowid DESC LIMIT 4""", (project_id, number))]

    def approve(self, user_id, project_id, number):
        current = self.revision(user_id, project_id)
        if current["number"] != number:
            raise ValueError("Эта кнопка относится к старой версии. Откройте текущий проект.")
        if not current["analysed"] or current["questions"]:
            raise ValueError("Сначала ответьте на уточнения.")
        brief = current["brief"]
        if not brief.description.strip() or any(v is None for v in
                (brief.width_mm, brief.height_mm, brief.depth_mm)):
            raise ValueError("Для чертежа нужны описание и ширина, высота, глубина.")
        if not self.attachments(user_id, project_id):
            raise ValueError("Сначала пришлите эскиз.")
        with self.connect() as con:
            con.execute("UPDATE revisions SET approved=1 WHERE project_id=? AND number=?",
                        (project_id, number))

    def artifact(self, user_id, project_id, number=None):
        project = self.project(user_id, project_id)
        number = project["revision"] if number is None else number
        with self.connect() as con:
            row = con.execute("SELECT * FROM artifacts WHERE project_id=? AND revision=?",
                              (project_id, number)).fetchone()
        return dict(row) if row else None

    def save_artifact(self, user_id, project_id, number, image, model):
        revision = self.revision(user_id, project_id, number)
        if not revision["approved"]:
            raise ValueError("Результат не относится к утверждённой версии.")
        path = self.directory / "results" / project_id / f"v{number}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(image)
        with self.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            existing = con.execute('SELECT sketch_number FROM artifacts WHERE project_id=? AND revision=?',
                                   (project_id, number)).fetchone()
            sketch_number = existing['sketch_number'] if existing else con.execute(
                'SELECT COALESCE(MAX(sketch_number),0)+1 FROM artifacts WHERE project_id=?',
                (project_id,)).fetchone()[0]
            con.execute('''INSERT INTO artifacts(project_id,revision,path,model,sketch_number)
                VALUES(?,?,?,?,?) ON CONFLICT(project_id,revision)
                DO UPDATE SET path=excluded.path,model=excluded.model''',
                (project_id, number, str(path), model, sketch_number))
            con.execute('UPDATE revisions SET sketch_error=NULL WHERE project_id=? AND number=?', (project_id, number))

    def set_error(self, user_id, project_id, number, kind, error):
        self.project(user_id, project_id)
        if kind not in ('analysis', 'sketch'):
            raise ValueError('Неизвестный тип обработки.')
        with self.connect() as con:
            con.execute(f'UPDATE revisions SET {kind}_error=? WHERE project_id=? AND number=?',
                        (error, project_id, number))

    def rename(self, user_id, project_id, title):
        self.project(user_id, project_id)
        title = title.strip()
        if not title or len(title) > 100 or any(ord(c) < 32 for c in title):
            raise ValueError('Название должно содержать от 1 до 100 символов в одной строке.')
        with self.connect() as con:
            con.execute('UPDATE projects SET title=? WHERE id=? AND user_id=?', (title, project_id, user_id))
        return self.project(user_id, project_id)

    def delete(self, user_id, project_id):
        self.project(user_id, project_id)
        if len(project_id) != 32 or any(c not in '0123456789abcdef' for c in project_id):
            raise ValueError('Некорректный идентификатор проекта.')
        # Только собственные каталоги проекта. Пути из записей БД не используются
        # для рекурсивного удаления; ссылки за пределы хранилища запрещены.
        folders = [self.directory / name / project_id for name in ('uploads', 'results')]
        root = self.directory.resolve()
        for folder in folders:
            if folder.is_symlink() or not folder.resolve().is_relative_to(root):
                raise ValueError('Небезопасный каталог проекта.')
        for folder in folders:
            if folder.exists():
                shutil.rmtree(folder)
        with self.connect() as con:
            for table in ('revisions', 'messages', 'attachments', 'artifacts', 'workflow_state', 'workflow_versions', 'workflow_messages'):
                con.execute(f'DELETE FROM {table} WHERE project_id=?', (project_id,))
            con.execute('DELETE FROM projects WHERE id=? AND user_id=?', (project_id, user_id))
            con.execute("UPDATE sessions SET project_id=NULL,screen='menu' WHERE user_id=? AND project_id=?", (user_id, project_id))
            # Не оставляем путь удалённого чертежа в сохранённой карточке.
            con.execute("UPDATE controls SET payload='{}' WHERE user_id=?", (user_id,))

    def control(self, user_id):
        with self.connect() as con:
            row = con.execute('SELECT * FROM controls WHERE user_id=?', (user_id,)).fetchone()
        if not row:
            return None
        result = dict(row)
        result['payload'] = json.loads(result['payload'])
        return result

    def save_control(self, user_id, chat_id, message_id, media, payload):
        with self.connect() as con:
            con.execute('INSERT OR REPLACE INTO controls VALUES(?,?,?,?,?)',
                        (user_id, chat_id, message_id, int(media), json.dumps(payload, ensure_ascii=False)))

    def forget_control(self, user_id):
        with self.connect() as con:
            con.execute('DELETE FROM controls WHERE user_id=?', (user_id,))

    def drawings(self, user_id, project_id, *, limit=20, offset=0):
        self.project(user_id, project_id)
        if not 1 <= limit <= 100 or offset < 0:
            raise ValueError('Некорректная страница списка чертежей.')
        with self.connect() as con:
            return [dict(row) for row in con.execute('''SELECT r.number,a.path,a.sketch_number FROM artifacts a
                JOIN revisions r ON r.project_id=a.project_id AND r.number=a.revision
                WHERE a.project_id=? ORDER BY r.number DESC LIMIT ? OFFSET ?''',
                (project_id, limit, offset))]
