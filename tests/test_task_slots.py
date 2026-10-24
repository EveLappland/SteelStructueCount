"""Real thread/reservation tests; no substituted model calls or answers."""
from concurrent.futures import ThreadPoolExecutor
import os
import threading
import unittest
from unittest.mock import patch

from app.task_slots import TaskSlots, ViewRequests, task_concurrency


class TaskConcurrencyTests(unittest.TestCase):
    def test_task_setting_is_independent_of_child_request_setting(self):
        with patch.dict(os.environ, {'LLM_TASK_CONCURRENCY':'10','LLM_MAX_CONCURRENCY':'5'}):
            self.assertEqual(task_concurrency({'max_concurrency':1}),10)
            self.assertEqual(task_concurrency({'max_task_concurrency':3}),3)
            for invalid in (False,0,-1,1.5,'bad'):
                with self.assertRaises(ValueError):
                    task_concurrency({'max_task_concurrency':invalid})

    def test_ten_shared_view_tasks_can_execute_five_requests_each(self):
        slots=TaskSlots(10)
        coordinators=[ViewRequests(slots,f'file-{i}',5) for i in range(3)]
        views=[(coordinators[i//4],f'view-{i%4}') for i in range(12)]
        reserved=[]
        for controller,view in views[:10]:
            for _ in range(5):
                self.assertTrue(controller.enter(view))
                reserved.append((controller,view))
            self.assertFalse(controller.enter(view))
        self.assertEqual(slots.counts()['active_tasks'],10)
        self.assertFalse(views[10][0].enter(views[10][1]))
        # Exercise fifty real concurrent operations behind production permits.
        barrier=threading.Barrier(51,timeout=10)
        release=threading.Event()
        lock=threading.Lock()
        active=0
        def operation(controller,view):
            nonlocal active
            try:
                with lock: active+=1
                barrier.wait()
                self.assertTrue(release.wait(10))
            finally:
                with lock: active-=1
                controller.leave(view)
        with ThreadPoolExecutor(max_workers=50) as pool:
            futures=[pool.submit(operation,*entry) for entry in reserved]
            try:
                barrier.wait()
                self.assertEqual(active,50)
                self.assertEqual(slots.counts()['peak_tasks'],10)
            finally:
                release.set()
            for future in futures: future.result()
        for controller,view in views[:10]: controller.finish(view)
        self.assertEqual(slots.counts()['active_tasks'],0)
        self.assertTrue(views[10][0].enter(views[10][1]))
        views[10][0].leave(views[10][1])
        for controller in coordinators:
            self.assertTrue(all(n<=5 for n in controller.peak_by_view.values()))
            controller.close()
        self.assertEqual(slots.counts()['active_tasks'],0)

    def test_request_and_view_lifetimes_are_separate_and_cleanup_releases_slots(self):
        slots=TaskSlots(1)
        first=ViewRequests(slots,'first',5)
        second=ViewRequests(slots,'second',5)
        self.assertTrue(first.enter('view'))
        with self.assertRaises(RuntimeError): first.finish('view')
        first.leave('view')
        # Master/expert phases retain the same view lease between requests.
        self.assertFalse(second.enter('other'))
        self.assertTrue(first.enter('view'))
        first.leave('view'); first.finish('view')
        with self.assertRaises(RuntimeError): first.leave('view')
        self.assertTrue(second.enter('other'))
        second.close()
        self.assertEqual(slots.counts()['active_tasks'],0)


if __name__=='__main__':
    unittest.main()
