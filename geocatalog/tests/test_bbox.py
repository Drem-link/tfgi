import pytest
from fastapi import HTTPException

from app.main import parse_bbox


def test_parse_bbox():
    assert parse_bbox("30,50,40,60") == (30.0, 50.0, 40.0, 60.0)


@pytest.mark.parametrize(
    "value",
    ["a,b,c,d", "181,0,190,10", "0,91,10,95", "40,50,30,60", "30,60,40,50", "nan,0,10,10"],
)
def test_invalid_bbox(value):
    with pytest.raises(HTTPException) as error:
        parse_bbox(value)
    assert error.value.status_code == 422
