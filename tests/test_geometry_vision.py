import unittest
from guarded_agent.geometry import ImageMapping, normalized_point, sendinput_absolute
from guarded_agent.model import Rect

try:
    from PIL import Image
except ImportError:
    Image = None


class GeometryTests(unittest.TestCase):
    def test_normalized_endpoints_stay_inside_target(self):
        r = Rect(10, 20, 101, 51)
        self.assertEqual(normalized_point(r, 0, 0), (10, 20))
        self.assertEqual(normalized_point(r, 1, 1), (110, 70))

    def test_negative_virtual_desktop_origin(self):
        r = Rect(-1920, -200, 1920, 1080)
        self.assertEqual(normalized_point(r, 0, 0), (-1920, -200))
        self.assertEqual(normalized_point(r, 1, 1), (-1, 879))

    def test_scaled_letterboxed_model_image(self):
        mapping = ImageMapping(Rect(-1000, 100, 1001, 501), Rect(20, 40, 501, 251), "frame-7")
        self.assertEqual(mapping.to_screen(270, 165, "frame-7"), (-500, 350))

    def test_letterbox_padding_rejected(self):
        mapping = ImageMapping(Rect(0, 0, 1000, 500), Rect(20, 40, 500, 250), "frame")
        with self.assertRaises(ValueError):
            mapping.to_screen(0, 0, "frame")

    def test_stale_observation_rejected(self):
        mapping = ImageMapping(Rect(0, 0, 1000, 500), Rect(0, 0, 500, 250), "before-move")
        with self.assertRaises(ValueError):
            mapping.to_screen(10, 10, "after-move")

    def test_dpi_is_not_applied_twice(self):
        # Rectangle already represents 125% physical geometry.
        self.assertEqual(normalized_point(Rect(125, 60, 126, 61), 1, 1), (250, 120))

    def test_sendinput_maps_full_virtual_desktop(self):
        desktop = Rect(-1920, -1080, 4480, 2680)
        self.assertEqual(sendinput_absolute(-1920, -1080, desktop), (0, 0))
        self.assertEqual(sendinput_absolute(2559, 1599, desktop), (65535, 65535))

    def test_bad_coordinates_are_not_clamped_into_a_click(self):
        for u in (-0.1, 1.1, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                normalized_point(Rect(0, 0, 100, 100), u, 0.5)
        with self.assertRaises(ValueError):
            sendinput_absolute(100, 0, Rect(0, 0, 100, 100))


@unittest.skipIf(Image is None, "Install Windows extras to test pixel verification")
class VisionTests(unittest.TestCase):
    def setUp(self):
        from guarded_agent.vision import changed_ratio
        self.diff = changed_ratio

    def test_small_result_region_change_is_detected(self):
        a = Image.new("RGB", (10, 10), "black")
        b = a.copy()
        b.putpixel((2, 2), (255, 255, 255))
        self.assertAlmostEqual(self.diff(a, b), 0.01)

    def test_caret_and_cursor_masks_remove_only_noise(self):
        a = Image.new("RGB", (10, 10), "black")
        b = a.copy()
        b.putpixel((2, 2), (255, 255, 255))
        self.assertEqual(self.diff(a, b, masks=(Rect(2, 2, 1, 1),)), 0)
        b.putpixel((8, 8), (255, 255, 255))
        self.assertGreater(self.diff(a, b, masks=(Rect(2, 2, 1, 1),)), 0)

    def test_small_rendering_noise_is_ignored(self):
        a = Image.new("RGB", (10, 10), (100, 100, 100))
        b = Image.new("RGB", (10, 10), (110, 110, 110))
        self.assertEqual(self.diff(a, b, tolerance=12), 0)

    def test_changed_geometry_requires_new_baseline(self):
        with self.assertRaises(ValueError):
            self.diff(Image.new("RGB", (10, 10)), Image.new("RGB", (20, 20)))

    def test_all_masked_is_not_success(self):
        a = Image.new("RGB", (10, 10))
        with self.assertRaises(ValueError):
            self.diff(a, a, masks=(Rect(0, 0, 10, 10),))

    def test_overlapping_and_outside_masks_do_not_double_count(self):
        a = Image.new("RGB", (10, 10), "black")
        b = a.copy()
        b.putpixel((9, 9), (255, 255, 255))
        self.assertAlmostEqual(self.diff(a, b, masks=(Rect(-1, -1, 3, 3), Rect(0, 0, 2, 2))), 1 / 96)
