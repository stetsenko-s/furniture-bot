"""Версии этапов и утверждения. Все данные принадлежат Telegram-приложению."""
import base64
import json
import uuid
from pathlib import Path
from contracts.models import Brief, ReferenceImage
from contracts.workflow import StagePlan, StageAnalysisRequest, StageImageRequest


def init_schema(con):
    con.executescript('''
        CREATE TABLE IF NOT EXISTS workflow_state(
            project_id TEXT PRIMARY KEY, stage TEXT NOT NULL DEFAULT 'sketch',
            version INTEGER, approved_sketch INTEGER);
        CREATE TABLE IF NOT EXISTS workflow_versions(
            project_id TEXT NOT NULL, stage TEXT NOT NULL, number INTEGER NOT NULL,
            source_stage TEXT NOT NULL, source_number INTEGER NOT NULL,
            base_brief TEXT NOT NULL, plan TEXT, analysed INTEGER NOT NULL DEFAULT 0,
            approved INTEGER NOT NULL DEFAULT 0, request_json TEXT,
            path TEXT, model TEXT, ordinal INTEGER, error TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(project_id,stage,number));
        CREATE TABLE IF NOT EXISTS workflow_messages(
            id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT NOT NULL,
            stage TEXT NOT NULL, version INTEGER NOT NULL, role TEXT NOT NULL,
            text TEXT NOT NULL, path TEXT, mime TEXT);
    ''')


class WorkflowStore:
    def __init__(self, store):
        self.store = store

    def state(self, uid, pid):
        self.store.project(uid, pid)
        with self.store.connect() as con:
            row = con.execute('SELECT * FROM workflow_state WHERE project_id=?', (pid,)).fetchone()
        return dict(row) if row else {'project_id': pid, 'stage': 'sketch', 'version': None, 'approved_sketch': None}

    def version(self, uid, pid, stage=None, number=None):
        state = self.state(uid, pid)
        stage = stage or state['stage']
        number = state['version'] if number is None else number
        with self.store.connect() as con:
            row = con.execute('SELECT * FROM workflow_versions WHERE project_id=? AND stage=? AND number=?', (pid, stage, number)).fetchone()
        if row is None:
            raise LookupError('Этап не найден.')
        value = dict(row)
        value['base_brief'] = Brief.model_validate_json(value['base_brief'])
        value['plan'] = StagePlan.model_validate_json(value['plan']) if value['plan'] else None
        return value

    def current(self, uid, pid, stage, number):
        state = self.state(uid, pid)
        return (state['stage'], state['version']) == (stage, number)

    def approve_sketch(self, uid, pid, revision):
        project = self.store.project(uid, pid)
        artifact = self.store.artifact(uid, pid, revision)
        if project['revision'] != revision or not artifact or not Path(artifact['path']).is_file():
            raise ValueError('Сначала создайте эскиз по последним уточнениям.')
        if self.state(uid, pid)['stage'] != 'sketch':
            raise ValueError('Вернитесь на этап эскиза.')
        with self.store.connect() as con:
            con.execute("INSERT INTO workflow_state(project_id,approved_sketch) VALUES(?,?) ON CONFLICT(project_id) DO UPDATE SET approved_sketch=excluded.approved_sketch", (pid, revision))

    def begin(self, uid, pid, stage, source_number):
        state = self.state(uid, pid)
        if stage == 'dimensions':
            if state['stage'] != 'sketch' or state['approved_sketch'] != source_number or self.store.project(uid, pid)['revision'] != source_number:
                raise ValueError('Сначала утвердите актуальный эскиз.')
            artifact = self.store.artifact(uid, pid, source_number)
            if not artifact or not Path(artifact['path']).is_file():
                raise ValueError('Нет исходного эскиза.')
            brief = self.store.generation_brief(uid, pid, source_number)
            source_stage, plan = 'sketch', None
        elif stage == 'render':
            source = self.version(uid, pid, 'dimensions', source_number)
            if state['stage'] != 'dimensions' or state['version'] != source_number or not source['approved'] or not source['path'] or not Path(source['path']).is_file():
                raise ValueError('Сначала утвердите актуальный чертёж с размерами.')
            brief, plan = source['plan'].brief, source['plan']
            source_stage = 'dimensions'
        else:
            raise ValueError('Неизвестный этап.')
        with self.store.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            previous = con.execute('SELECT number FROM workflow_versions WHERE project_id=? AND stage=? AND source_stage=? AND source_number=? ORDER BY number DESC LIMIT 1', (pid, stage, source_stage, source_number)).fetchone()
            if previous:
                number = previous['number']
            else:
                number = con.execute('SELECT COALESCE(MAX(number),0)+1 FROM workflow_versions WHERE project_id=? AND stage=?', (pid, stage)).fetchone()[0]
                con.execute('INSERT INTO workflow_versions(project_id,stage,number,source_stage,source_number,base_brief,plan) VALUES(?,?,?,?,?,?,?)',
                    (pid, stage, number, source_stage, source_number, brief.model_dump_json(), plan.model_dump_json() if plan else None))
            con.execute('UPDATE workflow_state SET stage=?,version=? WHERE project_id=?', (stage, number, pid))
        self.store.navigate(uid, 'review', pid)
        return self.version(uid, pid)

    def add_input(self, uid, pid, text, image=None, mime=None):
        old = self.version(uid, pid)
        path = None
        if image is not None:
            path = self.store.directory / 'uploads' / pid / (uuid.uuid4().hex + '.image')
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(image)
        with self.store.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            number = con.execute('SELECT COALESCE(MAX(number),0)+1 FROM workflow_versions WHERE project_id=? AND stage=?', (pid, old['stage'])).fetchone()[0]
            con.execute('INSERT INTO workflow_versions(project_id,stage,number,source_stage,source_number,base_brief,plan) VALUES(?,?,?,?,?,?,?)',
                (pid, old['stage'], number, old['source_stage'], old['source_number'], old['base_brief'].model_dump_json(), old['plan'].model_dump_json() if old['plan'] else None))
            con.execute('INSERT INTO workflow_messages(project_id,stage,version,role,text,path,mime) VALUES(?,?,?,?,?,?,?)', (pid, old['stage'], number, 'user', text, str(path) if path else None, mime))
            con.execute('UPDATE workflow_state SET version=? WHERE project_id=?', (number, pid))
        self.store.navigate(uid, 'review', pid)
        return number

    def messages(self, uid, pid, version):
        self.store.project(uid, pid)
        with self.store.connect() as con:
            return [dict(row) for row in con.execute('''SELECT m.* FROM workflow_messages m
                JOIN workflow_versions v ON v.project_id=m.project_id AND v.stage=m.stage AND v.number=m.version
                WHERE m.project_id=? AND m.stage=? AND m.version<=? AND v.source_stage=? AND v.source_number=? ORDER BY m.id''',
                (pid, version['stage'], version['number'], version['source_stage'], version['source_number']))]

    def references(self, uid, pid, version):
        if version['source_stage'] == 'sketch':
            source = self.store.artifact(uid, pid, version['source_number'])
        else:
            source = self.version(uid, pid, 'dimensions', version['source_number'])
        refs = [(source['path'], 'image/png')]
        uploads = [m for m in self.messages(uid, pid, version) if m['path']]
        if not uploads:
            uploads = self.store.attachments(uid, pid)
        for upload in uploads[-2:]:
            refs.append((upload['path'], upload['mime']))
        with self.store.connect() as con:
            previous = con.execute('SELECT path FROM workflow_versions WHERE project_id=? AND stage=? AND number<? AND source_stage=? AND source_number=? AND path IS NOT NULL ORDER BY number DESC LIMIT 1',
                (pid, version['stage'], version['number'], version['source_stage'], version['source_number'])).fetchone()
        if previous:
            refs.append((previous['path'], 'image/png'))
        return [ReferenceImage(mime_type=mime, data_base64=base64.b64encode(Path(path).read_bytes()).decode()) for path,mime in refs[:4]]

    def analysis_request(self, uid, pid, version):
        messages = self.store.messages(uid, pid) + self.messages(uid, pid, version)
        return StageAnalysisRequest(stage=version['stage'], brief=version['base_brief'], plan=version['plan'],
            messages=[{'role': m['role'], 'text': m['text']} for m in messages], references=self.references(uid, pid, version))

    def save_analysis(self, uid, pid, stage, number, plan):
        if not self.current(uid, pid, stage, number):
            return False
        reply = plan.reply
        if plan.questions:
            reply += '\nНужно уточнить:\n' + '\n'.join(f'{i}. {q}' for i,q in enumerate(plan.questions[:3],1))
        with self.store.connect() as con:
            con.execute('UPDATE workflow_versions SET plan=?,analysed=1,error=NULL WHERE project_id=? AND stage=? AND number=?', (plan.model_dump_json(), pid, stage, number))
            con.execute('INSERT INTO workflow_messages(project_id,stage,version,role,text) VALUES(?,?,?,?,?)', (pid, stage, number, 'assistant', reply))
        return True

    def request(self, uid, pid, stage, number):
        if not self.current(uid, pid, stage, number):
            raise ValueError('Это устаревшая кнопка. Откройте актуальный проект.')
        version = self.version(uid, pid)
        plan = version['plan']
        if not version['analysed'] or not plan or plan.questions or plan.needs_sketch_changes or not plan.inventory_complete:
            raise ValueError('Сначала ответьте на уточняющие вопросы.')
        if not plan.dimensions or any(d.value_mm is None for d in plan.dimensions):
            raise ValueError('Сначала уточните размеры.')
        if version['request_json']:
            return StageImageRequest.model_validate_json(version['request_json'])
        request = StageImageRequest(stage=stage, request_id=f'{pid}-{stage}-{number}', project_id=pid, revision=number,
            plan=plan, references=self.references(uid, pid, version))
        with self.store.connect() as con:
            con.execute('UPDATE workflow_versions SET request_json=?,error=NULL WHERE project_id=? AND stage=? AND number=?', (request.model_dump_json(), pid, stage, number))
        return request

    def save_image(self, uid, pid, stage, number, image, model):
        version = self.version(uid, pid, stage, number)
        if not version['request_json']:
            raise ValueError('Генерация не запрашивалась.')
        path = self.store.directory / 'results' / pid / f'{stage}-{number}.png'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(image)
        with self.store.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            ordinal = version['ordinal'] or con.execute('SELECT COALESCE(MAX(ordinal),0)+1 FROM workflow_versions WHERE project_id=? AND stage=?', (pid, stage)).fetchone()[0]
            con.execute('UPDATE workflow_versions SET path=?,model=?,ordinal=?,error=NULL WHERE project_id=? AND stage=? AND number=?', (str(path), model, ordinal, pid, stage, number))

    def error(self, uid, pid, stage, number, error):
        self.store.project(uid, pid)
        with self.store.connect() as con:
            con.execute('UPDATE workflow_versions SET error=? WHERE project_id=? AND stage=? AND number=?', (error, pid, stage, number))

    def prepare_reanalysis(self, uid, pid):
        """Повтор незавершённого анализа без повторного ввода и без смены картинки."""
        version = self.version(uid, pid)
        if version['path'] or version['request_json']:
            return False
        with self.store.connect() as con:
            con.execute('UPDATE workflow_versions SET analysed=0,error=NULL WHERE project_id=? AND stage=? AND number=?',
                        (pid, version['stage'], version['number']))
        return True

    def upgrade_drawing(self, uid, pid):
        """Old generated images remain in the gallery; a new draft gets geometry."""
        old = self.version(uid, pid)
        if (old['stage'] != 'dimensions' or not old['plan'] or old['plan'].drawing is not None
                or not (old['path'] or old['request_json'])):
            return False
        with self.store.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            number = con.execute('SELECT COALESCE(MAX(number),0)+1 FROM workflow_versions WHERE project_id=? AND stage=?', (pid, 'dimensions')).fetchone()[0]
            con.execute('INSERT INTO workflow_versions(project_id,stage,number,source_stage,source_number,base_brief,plan) VALUES(?,?,?,?,?,?,?)',
                        (pid, 'dimensions', number, old['source_stage'], old['source_number'], old['base_brief'].model_dump_json(), old['plan'].model_dump_json()))
            con.execute('UPDATE workflow_state SET version=? WHERE project_id=?', (number, pid))
        return True

    def approve(self, uid, pid, stage, number):
        if not self.current(uid, pid, stage, number):
            raise ValueError('Утвердить можно только текущий результат.')
        version = self.version(uid, pid)
        if not version['path'] or not Path(version['path']).exists():
            raise ValueError('Сначала получите изображение.')
        self.request(uid, pid, stage, number)
        with self.store.connect() as con:
            con.execute('UPDATE workflow_versions SET approved=1 WHERE project_id=? AND stage=? AND number=?', (pid, stage, number))

    def back(self, uid, pid):
        version = self.version(uid, pid)
        with self.store.connect() as con:
            con.execute('UPDATE workflow_state SET stage=?,version=? WHERE project_id=?',
                (version['source_stage'], version['source_number'] if version['source_stage'] != 'sketch' else None, pid))
        self.store.navigate(uid, 'review', pid)

    def sketch(self, uid, pid):
        self.store.project(uid, pid)
        with self.store.connect() as con:
            con.execute("UPDATE workflow_state SET stage='sketch',version=NULL WHERE project_id=?", (pid,))

    def drawings(self, uid, pid, limit=21, offset=0):
        self.store.project(uid, pid)
        if offset < 0:
            raise ValueError('Некорректная страница')
        with self.store.connect() as con:
            return [dict(row) for row in con.execute('''SELECT * FROM (
                SELECT 'sketch' AS stage,a.revision AS number,a.sketch_number AS ordinal,a.path,r.created_at AS stamp
                FROM artifacts a JOIN revisions r ON a.project_id=r.project_id AND a.revision=r.number WHERE a.project_id=?
                UNION ALL
                SELECT stage,number,ordinal,path,created_at AS stamp FROM workflow_versions WHERE project_id=? AND path IS NOT NULL
                ) ORDER BY stamp DESC,CASE stage WHEN 'render' THEN 3 WHEN 'dimensions' THEN 2 ELSE 1 END DESC,number DESC LIMIT ? OFFSET ?''', (pid,pid,limit,offset))]
