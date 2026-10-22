from pathlib import Path

import pytest

from clipforge.media.reframe import (
    FaceSpan,
    Shot,
    crop_center,
    plan_shots,
    sampling_args,
    shot_spans,
)

# A 9:16 crop of a 16:9 frame keeps this share of its width.
CROP_WIDTH = (9 / 16) / (16 / 9)


def test_shots_split_at_scene_cuts() -> None:
    assert shot_spans([4.0, 9.5], 20.0) == [(0.0, 4.0), (4.0, 9.5), (9.5, 20.0)]


def test_cuts_too_close_to_the_previous_cut_or_the_end_are_ignored() -> None:
    assert shot_spans([0.4, 4.0, 4.6, 19.5], 20.0) == [(0.0, 4.0), (4.0, 20.0)]


def test_a_clip_without_cuts_is_one_shot() -> None:
    assert shot_spans([], 75.0) == [(0.0, 75.0)]


def test_crop_is_centred_on_an_off_centre_face() -> None:
    center = crop_center([FaceSpan(0.25, 0.31)], CROP_WIDTH)

    assert center == pytest.approx(0.28)


def test_crop_holds_every_face_seen_in_the_shot() -> None:
    center = crop_center([FaceSpan(0.40, 0.45), FaceSpan(0.55, 0.60)], CROP_WIDTH)

    assert center == pytest.approx(0.50)


@pytest.mark.parametrize(
    ("face", "expected"),
    [(FaceSpan(0.0, 0.05), CROP_WIDTH / 2), (FaceSpan(0.95, 1.0), 1 - CROP_WIDTH / 2)],
)
def test_crop_stays_inside_the_frame(face: FaceSpan, expected: float) -> None:
    assert crop_center([face], CROP_WIDTH) == pytest.approx(expected)


def test_a_close_up_face_still_fits() -> None:
    assert crop_center([FaceSpan(0.38, 0.62)], CROP_WIDTH) == pytest.approx(0.50)


def test_a_face_wider_than_the_crop_shows_the_whole_frame() -> None:
    assert crop_center([FaceSpan(0.30, 0.70)], CROP_WIDTH) is None


def test_faces_too_far_apart_show_the_whole_frame() -> None:
    assert crop_center([FaceSpan(0.10, 0.18), FaceSpan(0.80, 0.88)], CROP_WIDTH) is None


def test_no_face_shows_the_whole_frame() -> None:
    assert crop_center([], CROP_WIDTH) is None


def test_each_shot_is_framed_from_its_own_samples() -> None:
    samples = [
        (0.0, [FaceSpan(0.20, 0.26)]),
        (0.5, [FaceSpan(0.21, 0.27)]),
        (4.0, [FaceSpan(0.10, 0.16), FaceSpan(0.84, 0.90)]),
        (8.5, []),
    ]

    shots = plan_shots([(0.0, 4.0), (4.0, 8.0), (8.0, 12.0)], samples, CROP_WIDTH)

    assert [(shot.start_s, shot.end_s) for shot in shots] == [(0.0, 4.0), (4.0, 8.0), (8.0, 12.0)]
    assert shots[0].crop_center == pytest.approx(0.235)
    assert [shot.crop_center for shot in shots[1:]] == [None, None]


def test_a_face_moving_across_the_shot_widens_its_crop() -> None:
    samples = [(0.0, [FaceSpan(0.10, 0.16)]), (1.0, [FaceSpan(0.70, 0.76)])]

    assert plan_shots([(0.0, 2.0)], samples, CROP_WIDTH) == [Shot(0.0, 2.0, None)]


def test_sampling_decodes_only_the_clip_range_once() -> None:
    args = sampling_args(Path("/src/in.mp4"), Path("/work"), 125.5, 155.25)

    assert args.index("-ss") < args.index("-i")
    assert args[args.index("-ss") + 1] == "125.500"
    assert args[args.index("-t") + 1] == "29.750"
    assert args.count("-i") == 1
    filters = args[args.index("-filter_complex") + 1]
    assert "fps=2" in filters
    assert "select='eq(n,0)+gt(scene,0.3)',showinfo" in filters
    assert str(Path("/work") / "frame_%05d.jpg") in args
