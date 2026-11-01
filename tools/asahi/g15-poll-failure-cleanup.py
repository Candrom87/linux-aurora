#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only OR MIT
"""Exercise actual G15 poll and failure cleanup against synthetic queues."""
import os
from pathlib import Path
import subprocess
import tempfile

HERE = Path(__file__).resolve()
ROOT = HERE.parents[2] if HERE.parent.name == 'asahi' else HERE.parent / 'linux-aurora'
G15 = ROOT / 'drivers/gpu/drm/asahi/g15'


def function(text, signature):
    start = text.index(signature)
    opening = text.index('{', start)
    depth = 0
    for i in range(opening, len(text)):
        if text[i] == '{':
            depth += 1
        elif text[i] == '}':
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    raise ValueError('unterminated function')


poll = (G15 / 'poll.rs').read_text()
recovery = (G15 / 'recovery.rs').read_text()
fixture = r'''
#![allow(dead_code)]
use std::sync::{Arc, Mutex, MutexGuard};
use std::sync::atomic::{AtomicBool, Ordering};
type Error = i32;
type Result<T = ()> = std::result::Result<T, Error>;
const EIO: Error = 5;
type Completions = Vec<(usize, Error)>;
static COMPLETED: Mutex<Vec<(usize, Error)>> = Mutex::new(Vec::new());
mod job {
    use super::*;
    pub fn complete_all(done: Completions) { COMPLETED.lock().unwrap().extend(done); }
}
fn push_completion(done: &mut Completions, job: &Arc<Job>, result: Result) {
    done.push((job.id, result.unwrap_err()));
}
fn msecs_to_jiffies(ms: u32) -> u32 { ms }
struct Job { id: usize, partly_published: bool }
struct Flight { job: Arc<Job>, group: Vec<Arc<Job>> }
struct Recovery { failed: Option<Error> }
impl Recovery { fn failed(&self) -> Option<Error> { self.failed } }
struct Engine {
    shared: Option<Arc<Shared>>, chan_wait: bool,
    pending: Vec<Arc<Job>>, flights: Vec<Flight>,
}
struct Firmware {
    engine: Engine, recovery: Recovery, outcome: Result<bool>,
    preempt_disarms: usize, preempt_fails: bool, vm_failures: usize, retained: Vec<usize>,
}
impl Firmware {
    fn poll(&mut self, _: bool, _: &mut Completions) -> Result<bool> { self.outcome }
    fn reclaim_delay_ns(&self) -> Option<u64> { None }
    fn preempt_disarm(&mut self) -> Result { self.preempt_disarms += 1; if self.preempt_fails { Err(EIO) } else { Ok(()) } }
    fn fail_all_vms(&mut self, _: Error) { self.vm_failures += 1; }
    fn job_partly_published(&self, job: &Job) -> bool { job.partly_published }
    fn retain(&mut self, job: &Arc<Job>) { self.retained.push(job.id); }
    FAIL_STOP
    FAIL_FLIGHTS
}
struct Lock(Mutex<Option<Box<Firmware>>>);
impl Lock { fn lock(&self) -> MutexGuard<'_, Option<Box<Firmware>>> { self.0.lock().unwrap() } }
struct Shared { state: Lock, evented: AtomicBool, latched: Mutex<Vec<Error>>, rearmed: Mutex<usize> }
impl Shared {
    fn fail_stop(&self, e: Error) { self.latched.lock().unwrap().push(e); }
    fn rearm(&self, _: bool) { *self.rearmed.lock().unwrap() += 1; }
    fn mod_poll(&self, _: u32) { *self.rearmed.lock().unwrap() += 1; }
    RUN_POLL
}
fn make(already_failed: bool) -> Arc<Shared> {
    let primary = Arc::new(Job { id: 1, partly_published: true });
    let grouped = Arc::new(Job { id: 2, partly_published: true });
    let pending = Arc::new(Job { id: 3, partly_published: false });
    let partial = Arc::new(Job { id: 4, partly_published: true });
    Arc::new(Shared {
        state: Lock(Mutex::new(Some(Box::new(Firmware {
            engine: Engine { shared: None, chan_wait: false,
                flights: if already_failed { vec![] } else { vec![Flight {job: primary, group: vec![grouped]}] },
                pending: if already_failed { vec![] } else { vec![pending, partial] } },
            recovery: Recovery { failed: if already_failed { Some(EIO) } else { None } },
            outcome: Err(EIO), preempt_disarms: 0, preempt_fails: false, vm_failures: 0, retained: vec![],
        })))), evented: AtomicBool::new(false), latched: Mutex::new(vec![]), rearmed: Mutex::new(0),
    })
}
#[test] fn unexpected_poll_error_fails_and_retains_all_owners() {
    COMPLETED.lock().unwrap().clear();
    let shared=make(false);
    shared.clone().run_poll();
    let guard=shared.state.lock(); let fw=guard.as_ref().unwrap();
    assert_eq!(fw.recovery.failed(), Some(EIO));
    assert_eq!(fw.preempt_disarms, 1);
    assert_eq!(fw.vm_failures, 1);
    assert!(fw.engine.pending.is_empty() && fw.engine.flights.is_empty());
    assert_eq!(fw.retained, vec![1,2,4]);
    assert_eq!(*COMPLETED.lock().unwrap(), vec![(1,EIO),(2,EIO),(3,EIO),(4,EIO)]);
    assert_eq!(*shared.latched.lock().unwrap(), vec![EIO]);
}
#[test] fn already_failed_poll_does_not_repeat_cleanup() {
    COMPLETED.lock().unwrap().clear();
    let shared=make(true); shared.clone().run_poll();
    let guard=shared.state.lock(); let fw=guard.as_ref().unwrap();
    assert_eq!(fw.preempt_disarms, 0);
    assert_eq!(fw.vm_failures, 0);
    assert!(COMPLETED.lock().unwrap().is_empty());
    assert_eq!(*shared.latched.lock().unwrap(), vec![EIO]);
}
#[test] fn disarm_failure_does_not_skip_cleanup() {
    COMPLETED.lock().unwrap().clear();
    let shared=make(false);
    shared.state.lock().as_deref_mut().unwrap().preempt_fails=true;
    shared.clone().run_poll();
    let guard=shared.state.lock(); let fw=guard.as_ref().unwrap();
    assert_eq!(fw.recovery.failed(), Some(EIO));
    assert_eq!(fw.retained, vec![1,2,4]);
    assert_eq!(*COMPLETED.lock().unwrap(), vec![(1,EIO),(2,EIO),(3,EIO),(4,EIO)]);
}
'''.replace('RUN_POLL', function(poll, '    pub(crate) fn run_poll(')).replace(
    'FAIL_FLIGHTS', function(recovery, '    pub(crate) fn fail_flights(')).replace(
    'FAIL_STOP', function(recovery, '    pub(crate) fn fail_stop('))

with tempfile.TemporaryDirectory(prefix='g15-poll-failure-') as tmp:
    source, binary = Path(tmp) / 'test.rs', Path(tmp) / 'test'
    source.write_text(fixture)
    subprocess.run([os.environ.get('RUSTC', 'rustc'), '--edition=2021', '--test',
                    str(source), '-o', str(binary)], check=True)
    subprocess.run([str(binary), '--test-threads=1'], check=True)
