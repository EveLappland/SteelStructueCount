"""Request construction and payload bounds, without simulated model replies."""
import unittest

from app.grid_workflow import Task, format_retry_task


class RequestRetryTests(unittest.TestCase):
    def test_failed_text_is_hashed_not_reinjected_or_accumulated(self):
        task=Task('view-q','master',{'image_path':'source.png'},'ORIGINAL_INPUT')
        large='NOT_A_DRAWING_FACT_'*10000
        retry=format_retry_task(task,'format error',large)
        self.assertEqual(retry.entry,task.entry)
        self.assertEqual(retry.contract,task.contract)
        self.assertEqual(retry.format_retry,1)
        self.assertNotIn(large,retry.prompt)
        self.assertIn('previous_reply_sha256',retry.prompt)
        self.assertLess(len(retry.prompt),3000)
        again=format_retry_task(retry,'another failure','OTHER_FAILURE')
        self.assertEqual(again.base_prompt,'ORIGINAL_INPUT')
        self.assertEqual(again.prompt.count('ORIGINAL_INPUT'),1)
        self.assertNotIn('format error',again.prompt)
        self.assertNotIn('OTHER_FAILURE',again.prompt)
        self.assertEqual(again.format_retry,2)


if __name__=='__main__':unittest.main()
