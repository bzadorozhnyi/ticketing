use std::time::Duration;

use gateway::rate_limit::{Decision, LocalBuckets};
use tokio::time::Instant;

const PER_SECOND: f64 = 2.0;
const BURST: u32 = 3;
/// An empty bucket fills in `BURST / PER_SECOND`.
const FILL_TIME: Duration = Duration::from_millis(1500);

fn buckets(max_users: usize) -> LocalBuckets {
    LocalBuckets::new(PER_SECOND, BURST, max_users)
}

fn take_all(buckets: &LocalBuckets, user: &str, times: usize, now: Instant) -> usize {
    (0..times)
        .filter(|_| buckets.take(user, now) == Decision::Allowed)
        .count()
}

#[test]
fn a_user_gets_the_burst_then_waits_for_a_token() {
    let buckets = buckets(10);
    let now = Instant::now();

    assert_eq!(take_all(&buckets, "user", 3, now), 3);

    assert_eq!(
        buckets.take("user", now),
        Decision::Limited {
            retry_after: Duration::from_millis(500)
        }
    );
}

#[test]
fn tokens_come_back_at_the_rate_up_to_the_burst() {
    let buckets = buckets(10);
    let now = Instant::now();
    take_all(&buckets, "user", 3, now);

    assert_eq!(
        take_all(&buckets, "user", 5, now + Duration::from_secs(1)),
        2
    );
    assert_eq!(
        take_all(&buckets, "user", 5, now + Duration::from_secs(60)),
        3
    );
}

#[test]
fn a_full_map_leaves_a_new_user_to_valkey() {
    let buckets = buckets(2);
    let now = Instant::now();
    take_all(&buckets, "first", 1, now);
    take_all(&buckets, "second", 1, now);

    assert_eq!(take_all(&buckets, "third", 10, now), 10);
}

#[test]
fn idle_users_make_room_and_active_ones_keep_their_limit() {
    let buckets = buckets(2);
    let now = Instant::now();
    take_all(&buckets, "idle", 1, now);
    take_all(&buckets, "active", 3, now);
    let later = now + FILL_TIME - Duration::from_millis(1);
    take_all(&buckets, "active", 2, later);

    assert_eq!(take_all(&buckets, "new", 10, later), 3);
    assert_eq!(take_all(&buckets, "active", 10, later), 0);
}

#[test]
fn the_map_is_scanned_at_most_once_per_fill_time() {
    let buckets = buckets(1);
    let now = Instant::now();
    take_all(&buckets, "user", 1, now);
    take_all(&buckets, "first", 1, now);
    let idle_but_too_soon = now + Duration::from_secs(1);

    assert_eq!(
        take_all(&buckets, "second", 10, idle_but_too_soon),
        10,
        "the scan for the first new user was a second ago"
    );
    assert_eq!(take_all(&buckets, "third", 10, now + FILL_TIME), 3);
}
