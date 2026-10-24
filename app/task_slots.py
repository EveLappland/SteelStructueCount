"""Two independent limits: active view tasks, requests inside each view."""
import os
import threading
from collections import Counter


def task_concurrency(settings=None):
    value = (settings or {}).get('max_task_concurrency')
    if value in (None, ''):
        value = os.getenv('LLM_TASK_CONCURRENCY') or '10'
    if isinstance(value, bool) or not str(value).isdigit() or int(value) < 1:
        raise ValueError('主程序task并发数必须是正整数。')
    return int(value)


class TaskSlots:
    """Shared by PDF coordinators; a lease lasts the complete view workflow."""
    def __init__(self, limit):
        self.limit = limit
        self.active = set()
        self.peak = 0
        self.condition = threading.Condition()

    def acquire(self, identity):
        with self.condition:
            if identity in self.active:
                return True
            if len(self.active) >= self.limit:
                return False
            self.active.add(identity)
            self.peak = max(self.peak, len(self.active))
            return True

    def release(self, identity):
        with self.condition:
            self.active.discard(identity)
            self.condition.notify_all()

    def wait(self, timeout=.1):
        with self.condition:
            self.condition.wait(timeout)

    def counts(self):
        with self.condition:
            return {'active_tasks': len(self.active), 'peak_tasks': self.peak,
                    'task_limit': self.limit}


class ViewRequests:
    """One PDF coordinator's view leases and in-flight request reservations."""
    def __init__(self, slots, prefix, per_view):
        self.slots, self.prefix, self.per_view = slots, prefix, per_view
        self.admitted = set()
        self.pending = Counter()
        self.peak_by_view = Counter()
        self.lock = threading.Lock()

    def enter(self, view):
        with self.lock:
            if self.pending[view] >= self.per_view:
                return False
            if view not in self.admitted:
                if not self.slots.acquire(self.prefix+':'+view):
                    return False
                self.admitted.add(view)
            self.pending[view] += 1
            self.peak_by_view[view] = max(self.peak_by_view[view], self.pending[view])
            return True

    def leave(self, view):
        with self.lock:
            if self.pending[view] < 1:
                raise RuntimeError('Request reservation released twice')
            self.pending[view] -= 1

    def finish(self, view):
        with self.lock:
            if self.pending[view]:
                raise RuntimeError('View still has in-flight requests')
            if view in self.admitted:
                self.admitted.remove(view)
                self.slots.release(self.prefix+':'+view)

    def close(self):
        # Used after executor shutdown, including exception cleanup.
        with self.lock:
            for view in self.admitted:
                self.slots.release(self.prefix+':'+view)
            self.admitted.clear()
