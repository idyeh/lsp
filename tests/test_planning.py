from app.enums import PresentationPreference as P
from app.enums import TopicType
from app.services import planning
from app.services.results import compute_results


def make_plan(db, **targets):
    return planning.generate_candidate_plan(db, **targets)


def vote(make_user, make_preference, topic, interest_levels=(), leads=0, co=0, maybes=0):
    """Add one voter per interest level and extra volunteers with the given presentation preference."""
    n = topic.id * 100
    for level in interest_levels:
        n += 1
        make_preference(make_user(f"v{n}"), topic, interest=level)
    for count, pref in ((leads, P.lead), (co, P.co_present), (maybes, P.maybe)):
        for _ in range(count):
            n += 1
            make_preference(make_user(f"v{n}"), topic, interest=0, presenting=pref)


def test_all_active_core_topics_are_included_even_with_no_votes(db, make_topic, make_user, make_preference):
    core = [make_topic(f"Core {i}", TopicType.core) for i in range(4)]
    popular = make_topic("Popular")
    vote(make_user, make_preference, popular, [3, 3, 3], leads=1)
    plan = make_plan(db)
    assert plan.core_topics == [t.id for t in core]  # zero interest does not exclude core topics
    assert plan.recommended_optionals == [popular.id]


def test_core_topics_are_included_beyond_the_core_target(db, make_topic):
    for i in range(5):
        make_topic(f"Core {i}", TopicType.core)
    plan = make_plan(db, target_core=4)
    assert len(plan.core_topics) == 5
    assert "CORE_COUNT_MISMATCH" in {w.type for w in plan.warnings}


def test_inactive_topics_never_appear(db, make_topic):
    make_topic("Draft core", TopicType.core, status="draft")
    make_topic("Archived optional", status="archived")
    make_topic("Closed optional", status="closed")
    plan = make_plan(db)
    assert plan.core_topics == [] and plan.ranked_optionals == []


def test_optionals_ranked_by_interest_score(db, make_topic, make_user, make_preference):
    a, b, c = make_topic("A"), make_topic("B"), make_topic("C")
    for topic, levels in ((a, [1]), (b, [3, 3]), (c, [2, 2, 1])):  # scores 1, 6, 5
        vote(make_user, make_preference, topic, levels, leads=1)
    assert [r.topic.title for r in make_plan(db).ranked_optionals] == ["B", "C", "A"]


def test_topics_with_a_lead_or_co_presenter_are_favoured_over_higher_scores(db, make_topic, make_user, make_preference):
    popular_no_presenter = make_topic("Popular but nobody to present")
    modest_with_presenter = make_topic("Modest with co-presenter")
    only_maybe = make_topic("Only a maybe")
    vote(make_user, make_preference, popular_no_presenter, [3] * 10)
    vote(make_user, make_preference, modest_with_presenter, [1], co=1)
    vote(make_user, make_preference, only_maybe, [3] * 5, maybes=2)  # a 'maybe' does not count as available
    ranked = [r.topic.title for r in make_plan(db).ranked_optionals]
    assert ranked == ["Modest with co-presenter", "Popular but nobody to present", "Only a maybe"]


def test_ties_are_broken_by_lead_count_then_topic_id(db, make_topic, make_user, make_preference):
    first, second, third = make_topic("First"), make_topic("Second"), make_topic("Third")
    vote(make_user, make_preference, first, [2], co=1)
    vote(make_user, make_preference, second, [2], leads=1)
    vote(make_user, make_preference, third, [2], leads=1)
    ranked = [r.topic.title for r in make_plan(db).ranked_optionals]
    assert ranked == ["Second", "Third", "First"]  # equal scores; leads first, then lowest id


def test_recommendation_and_alternatives_follow_targets(db, make_topic, make_user, make_preference):
    topics = [make_topic(f"T{i}") for i in range(7)]
    for i, topic in enumerate(topics):
        vote(make_user, make_preference, topic, [3] * (7 - i), leads=1)  # T0 is best
    plan = make_plan(db, target_optional=4)
    assert plan.recommended_optionals == [t.id for t in topics[:4]]
    assert plan.alternative_ids == [topics[4].id, topics[5].id]


def test_warnings_for_missing_presenters(db, make_topic, make_user, make_preference):
    core_none = make_topic("Core nobody", TopicType.core)
    core_ok = make_topic("Core ok", TopicType.core)
    opt_no_lead = make_topic("Opt co only")
    opt_none = make_topic("Opt nobody")
    vote(make_user, make_preference, core_ok, [], leads=1)
    vote(make_user, make_preference, opt_no_lead, [3, 3], co=1)
    vote(make_user, make_preference, opt_none, [1])
    plan = make_plan(db, target_total=4, target_core=2, target_optional=2)
    warnings = {(w.topic_id, w.type) for w in plan.warnings}
    assert warnings == {
        (core_none.id, "NO_PRESENTER"),
        (opt_no_lead.id, "NO_LEAD_PRESENTER"),
        (opt_none.id, "NO_PRESENTER"),
    }


def test_target_total_mismatch_is_reported(db, make_topic):
    make_topic("Only optional")
    plan = make_plan(db, target_total=8, target_core=0, target_optional=4)
    assert [w.type for w in plan.warnings if w.topic_id is None] == ["TARGET_TOTAL_MISMATCH"]


def test_recommendation_is_deterministic(db, make_topic, make_user, make_preference):
    for i in range(6):
        vote(make_user, make_preference, make_topic(f"T{i}"), [2, 1], leads=i % 2)
    first = make_plan(db)
    second = make_plan(db)
    assert first.core_topics == second.core_topics
    assert first.recommended_optionals == second.recommended_optionals
    assert first.alternative_ids == second.alternative_ids
    assert first.warnings == second.warnings


def test_default_targets_come_from_settings(db, make_topic):
    from app.services import settings as settings_service

    for i in range(6):
        make_topic(f"T{i}")
    settings_service.update(db, {"target_optional_count": 2})
    assert len(make_plan(db).recommended_optionals) == 2
    assert len(make_plan(db, target_optional=5).recommended_optionals) == 5


def test_generate_endpoint_is_read_only_and_organiser_only(login, db, student, organiser, make_topic):
    from sqlalchemy import func, select

    from app.models import SeminarSession

    core = make_topic("Core", TopicType.core)
    opt = make_topic("Optional")
    assert login(student).post("/api/plans/candidate/generate").status_code == 403

    response = login(organiser).post(
        "/api/plans/candidate/generate", json={"target_total": 2, "target_core": 1, "target_optional": 1}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["core_topics"] == [core.id]
    assert body["recommended_optionals"] == [opt.id]
    assert body["alternatives"] == []
    assert {"topic_id": core.id, "type": "NO_PRESENTER"} == {k: v for k, v in body["warnings"][0].items() if k != "message"}
    assert body["ranking"][0]["topic_id"] == opt.id
    assert db.scalar(select(func.count()).select_from(SeminarSession)) == 0  # nothing was created


def test_generate_endpoint_works_without_a_body(login, organiser):
    assert login(organiser).post("/api/plans/candidate/generate").status_code == 200


def test_ranking_uses_aggregates_of_active_users_only(db, make_topic, make_user, make_preference):
    a, b = make_topic("A"), make_topic("B")
    ghost = make_user("ghost")
    make_preference(ghost, a, interest=3, presenting=P.lead)
    make_preference(make_user("real"), b, interest=1, presenting=P.lead)
    ghost.is_active = False
    db.commit()
    assert [r.topic.title for r in make_plan(db).ranked_optionals] == ["B", "A"]
    assert [r.interest_score for r in compute_results(db, [a, b])] == [1, 0]
