import cv2
import numpy as np

from homeworkflow.omr.recognize import classify_slot


def test_blank_and_filled_review():
    blank = np.full((60, 60), 255, dtype=np.uint8)
    filled = np.zeros((60, 60), dtype=np.uint8)
    assert classify_slot(blank)[0] == "blank"
    assert classify_slot(filled)[0] == "review"


def test_slashes_and_x_are_classified_after_border_inset():
    forward = np.full((60, 60), 255, dtype=np.uint8)
    back = forward.copy()
    cv2.line(forward, (10, 50), (50, 10), 0, 4)
    cv2.line(back, (10, 10), (50, 50), 0, 4)
    crossed = np.minimum(forward, back)
    assert classify_slot(forward)[0] == "slash_forward"
    assert classify_slot(back)[0] == "slash_back"
    assert classify_slot(crossed)[0] == "x"
