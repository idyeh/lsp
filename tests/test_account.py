"""The account page: users change their own password."""

from tests.conftest import PASSWORD

NEW = "a-brand-new-password"


def change(b, current=PASSWORD, new=NEW, confirm=None):
    return b.post(
        "/account/password",
        {"current_password": current, "new_password": new, "confirm_password": new if confirm is None else confirm},
        page="/account",
    )


def test_account_page_shows_who_is_signed_in(browser, student):
    page = browser(student).get("/account").text
    assert "Sam Student" in page and "student1" in page and "Change your password" in page


def test_account_page_needs_login(browser):
    assert browser().get("/account").headers["location"] == "/login"


def test_user_changes_their_password_through_the_page(browser, student):
    b = browser(student)
    assert "Your password was changed" in b.follow(change(b)).text
    assert b.get("/topics").status_code == 200  # still signed in on this device
    assert browser().login(student, PASSWORD).headers["location"] == "/login"  # the old password is gone
    assert browser().login(student, NEW).headers["location"] == "/"


def test_wrong_current_password_is_refused(browser, student):
    b = browser(student)
    assert "The current password is incorrect" in b.follow(change(b, current="not-my-password")).text
    assert browser().login(student, PASSWORD).headers["location"] == "/"


def test_mismatched_confirmation_is_refused(browser, student):
    b = browser(student)
    assert "The new passwords do not match" in b.follow(change(b, confirm="something-else-entirely")).text
    assert browser().login(student, PASSWORD).headers["location"] == "/"


def test_weak_and_unchanged_passwords_are_refused(browser, student):
    b = browser(student)
    assert "Password must be between 8 and 256" in b.follow(change(b, new="short")).text
    assert "must be different from the current one" in b.follow(change(b, new=PASSWORD)).text


def test_changing_the_password_signs_out_other_devices(browser, student):
    phone, laptop = browser(student), browser(student)
    assert laptop.get("/topics").status_code == 200
    change(phone)
    assert laptop.get("/topics").headers["location"] == "/login"
    assert phone.get("/topics").status_code == 200


def test_an_organiser_resetting_their_own_password_stays_signed_in(browser, organiser):
    o = browser(organiser)
    response = o.post(f"/organiser/users/{organiser.id}/password", {"password": "organiser-new-password"}, page="/organiser/users")
    assert "Password changed" in o.follow(response).text
    assert o.get("/organiser/users").status_code == 200


def test_an_organiser_resetting_a_students_password_signs_that_student_out(browser, student, organiser):
    s, o = browser(student), browser(organiser)
    assert s.get("/topics").status_code == 200
    o.post(f"/organiser/users/{student.id}/password", {"password": "set-by-organiser-1"}, page="/organiser/users")
    assert s.get("/topics").headers["location"] == "/login"
    assert browser().login(student, "set-by-organiser-1").headers["location"] == "/"


def test_account_page_in_chinese(browser, student):
    b = browser(student, headers={"Accept-Language": "zh-CN"})
    assert "你的密码已修改" in b.follow(change(b)).text
    assert "当前密码不正确" in b.follow(change(b, current="not-my-password")).text
