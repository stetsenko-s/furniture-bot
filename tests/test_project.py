from services.dialogue.src.rules import analysis_result
import base64
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from pydantic import ValidationError

from bot.store import Store
from contracts.models import Brief, ReferenceImage
from services.dialogue.src.models import Clarification, DialogueResult

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl6VEkAAAAASUVORK5CYII=")


def completed(description="Тумба с двумя дверцами"):
    return DialogueResult(brief=Brief(title="Тумба", description=description,
        width_mm=1000, height_mm=800, depth_mm=500,
        material=None, color=None, hardware=None),
        reply="Описание готово к проверке.", questions=[], is_cabinet=True,
        cabinet_sections=[dict(name='Тумба', doors='present', no_doors_user_quote=None,
                               door_details_known=True, interior_known=True)])


class ProjectTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(self.temp.name)
        self.project = self.store.create(10)
        self.pid = self.project["id"]

    def input(self, text="Тумба", image=PNG):
        return self.store.add_input(10, self.pid, text, image, "image/png")

    def prepare(self):
        revision = self.input()
        self.store.save_analysis(10, self.pid, revision, analysis_result(completed(), self.store.messages(10, self.pid)))
        return revision

    def test_restart_preserves_dialogue_and_image(self):
        number = self.prepare()
        restarted = Store(self.temp.name)
        self.assertEqual(restarted.session(10)["project_id"], self.pid)
        self.assertEqual(restarted.revision(10, self.pid)["brief"].width_mm, 1000)
        self.assertEqual(len(restarted.messages(10, self.pid)), 2)
        self.assertEqual(Path(restarted.attachments(10, self.pid)[0]["path"]).read_bytes(), PNG)
        self.assertEqual(restarted.revision(10, self.pid)["number"], number)

    def test_other_users_cannot_read_or_modify_project(self):
        for operation in (lambda: self.store.revision(11, self.pid),
                          lambda: self.store.messages(11, self.pid),
                          lambda: self.store.add_input(11, self.pid, "Изменение"),
                          lambda: self.store.approve(11, self.pid, 1),
                          lambda: self.store.navigate(11, "review", self.pid)):
            with self.assertRaises(LookupError):
                operation()

    def test_back_and_menu_do_not_delete_answers_or_approval(self):
        number = self.prepare()
        self.store.approve(10, self.pid, number)
        for screen in ("review", "intake", "menu"):
            self.store.navigate(10, screen)
            current = self.store.revision(10, self.pid)
            self.assertEqual(current["number"], number)
            self.assertTrue(current["approved"])
            self.assertEqual(current["brief"].description, completed().brief.description)

    def test_edit_creates_version_and_invalidates_approval(self):
        number = self.prepare()
        self.store.approve(10, self.pid, number)
        self.store.save_artifact(10, self.pid, number, PNG, "test")
        newer = self.input("Добавь полку", image=None)
        self.assertGreater(newer, number)
        self.assertFalse(self.store.revision(10, self.pid)["approved"])
        self.assertFalse(self.store.revision(10, self.pid)["analysed"])
        self.assertIsNone(self.store.artifact(10, self.pid))
        self.assertIsNotNone(self.store.artifact(10, self.pid, number))
        self.assertTrue(self.store.revision(10, self.pid, number)["approved"])

    def test_stale_confirmation_does_not_generate_newer_version(self):
        number = self.prepare()
        self.input("Высота 900", image=None)
        with self.assertRaisesRegex(ValueError, "старой версии"):
            self.store.approve(10, self.pid, number)

    def test_late_analysis_cannot_replace_new_input(self):
        number = self.input()
        self.input("Высота 900", image=None)
        with self.assertRaises(ValueError):
            self.store.save_analysis(10, self.pid, number, analysis_result(completed(), self.store.messages(10, self.pid)))

    def test_late_image_saved_to_original_approved_version(self):
        number = self.prepare()
        self.store.approve(10, self.pid, number)
        newer = self.input("Новый размер", image=None)
        self.store.save_artifact(10, self.pid, number, PNG, "test")
        self.assertIsNone(self.store.artifact(10, self.pid, newer))
        self.assertIsNotNone(self.store.artifact(10, self.pid, number))

    def test_confirmation_requires_dimensions(self):
        number = self.input()
        response = completed()
        response.brief.width_mm = None
        self.store.save_analysis(10, self.pid, number, analysis_result(response, self.store.messages(10, self.pid)))
        with self.assertRaisesRegex(ValueError, "ширина"):
            self.store.approve(10, self.pid, number)

    def test_confirmation_requires_sketch_and_resolved_questions(self):
        number = self.input(image=None)
        self.store.save_analysis(10, self.pid, number, analysis_result(completed(), self.store.messages(10, self.pid)))
        with self.assertRaisesRegex(ValueError, "эскиз"):
            self.store.approve(10, self.pid, number)
        number = self.input()
        response = completed()
        response.questions = [Clarification(text="Одна или две двери?", topic="doors", section_index=0)]
        self.store.save_analysis(10, self.pid, number, analysis_result(response, self.store.messages(10, self.pid)))
        with self.assertRaisesRegex(ValueError, "уточнения"):
            self.store.approve(10, self.pid, number)

    def test_new_project_does_not_overwrite_previous(self):
        number = self.prepare()
        new = self.store.create(10)
        self.assertNotEqual(new["id"], self.pid)
        self.assertEqual(self.store.revision(10, self.pid)["number"], number)
        self.assertEqual(self.store.session(10)["project_id"], new["id"])

    def test_nonfinite_and_negative_dimensions_rejected(self):
        for value in (float("nan"), float("inf"), -1, 0):
            data = completed().brief.model_dump()
            data["width_mm"] = value
            with self.assertRaises(ValidationError):
                Brief.model_validate(data)

    def test_disguised_document_and_invalid_base64_rejected(self):
        for content in ("!!bad", base64.b64encode(b"<script>not an image</script>").decode()):
            with self.assertRaises(ValidationError):
                ReferenceImage(mime_type="image/png", data_base64=content)
