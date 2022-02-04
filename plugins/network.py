"""
Commands that involve network requests.

Exported commands:
    MdeConvertBareLinkToMdLinkCommand
    MdeConvertBareLinkToMdLinkWholeviewCommand
"""
import sublime
import re
import traceback
import threading
import queue
import urllib
import urllib.request

from .view import MdeTextCommand
from .view import find_by_selector_in_regions
from .references import suggest_default_link_name

from .asynctasks import TaskQueue

barelink_scope_name = "meta.link.inet.markdown"


class QueueWorker(threading.Thread):
    """A worker that executes a specific function on all the work
    in a given queue."""

    def __init__(self, queue, fn, *args, **kwargs):
        """Summary
        
        Args:
            queue: Queue to draw work from.
            fn: Function executed
            *args: Passthough to threading.Thread
            **kwargs: Passthough to threading.Thread
        """
        self.queue = queue
        self.fn = fn
        super().__init__(*args, **kwargs)

    def run(self):
        """Run fn on items popped from q until queue is empty."""
        while True:
            try:
                work = self.queue.get(timeout=3)  # 3s timeout
            except queue.Empty:
                return
            self.fn(*work)
            self.queue.task_done()

def do_work_helper(workfn, inputs, max_threads=8):
    """Use threads and a queue to parallelize work done by a function."""
    # Create a queue. (Everything following has q in the namespace)
    q = queue.Queue()

    results = []

    def _process(*args):
        try:
            ret = workfn(*args)
        except Exception as e:
            traceback.print_exc()
            ret = e
        results.append(ret)

    for args in inputs:
        # Replace with actual function arguments
        q.put_nowait(args)

    # Start threads
    for _ in range(max_threads):
        QueueWorker(q, _process).start()

    # Block until the queue is empty.
    q.join()  

    return results

class MdeConvertBareLinkToMdLinkCommand(MdeTextCommand):
    """Convert an inline link to reference."""

    def is_visible(self):
        """Return True if selection contains links"""
        view = self.view
        for sel in view.find_by_selector(barelink_scope_name):
            if any(s.intersects(sel) for s in view.sel()):
                return True
        return False

    def run(self, edit, name=None):
        """Run command callback."""

        url_titles = {}
        url_redirects = {}

        view = self.view
        valid_regions = find_by_selector_in_regions(view, view.sel(), barelink_scope_name)

        def getInfoFromUrlJob(link_href):
            try:
                resp = urllib.request.urlopen(link_href)
            except urllib.error.HTTPError as e:
                print(link_href)
                if e.url != link_href:
                    print(link_href, "=/=", e.url, "Redirect?")
                    url_redirects[link_href] = e.url
                raise

            content_type = {a: b for a, b in resp.getheaders()}.get("Content-Type")
            if content_type and not content_type.startswith("text"):
                url_titles[link_href] = None
                raise TypeError(
                    "Link '{}' points to non-text content '{}'".format(link_href, content_type)
                )

            match = re.search(rb"<title[^>]*>(?!<)(.+?)</title>", resp.read())
            if match:
                url_titles[link_href] = re.sub(r"([\[\]])", r"\\\g<1>", match.group(1).decode())

            real_url = resp.geturl()
            if real_url != link_href:
                print(link_href, "=/=", real_url, "Redirect?")
                url_redirects[link_href] = real_url

        def finish(*args):
            print(args)
            view.erase_status("rawlinktomd")

            for link_region in valid_regions[::-1]:
                link_href = view.substr(link_region)
                suggested_title = suggest_default_link_name(
                    "", url_redirects.get(link_href, link_href), False
                )
                # print("Getting info from", link_href)
                # try:
                #     getInfoFromUrlJob(link_href)
                # except Exception as e:
                #     print(e)

                print("Processing", link_href)
                if url_titles.get(link_href):
                    title = url_titles[link_href] + " (" + suggested_title + ")"
                else:
                    print("Link '{}' has NoneType as value".format(link_href))
                    title = suggested_title

                link_href = url_redirects.get(link_href) or link_href
                view.replace(edit, link_region, "[" + title + "](" + link_href + ")")

        # This doesn't work, because we can't execute edit tasks after the run
        # function ends.

        # thread_queue = TaskQueue()
        # thread_queue.start()

        # for link_region in valid_regions:
        #     link_href = view.substr(link_region)
        #     thread_queue.execute_async(getTitleFromUrlJob, link_href)

        # thread_queue.execute_async(finish)

        do_work_helper(
            getInfoFromUrlJob,
            [(view.substr(link_region),) for link_region in valid_regions]
        )

        # for link_region in valid_regions:

        #     link_href = view.substr(link_region)
        #     try:
        #         getTitleFromUrlJob(link_href)
        #     except Exception:
        #         traceback.print_exc()
        #         pass

        finish()


class MdeConvertBareLinkToMdLinkWholeviewCommand(MdeTextCommand):
    """Convert all inline links to reference."""

    def is_visible(self):
        return True

    def run(self, edit, name=None):
        self.view.sel().add(sublime.Region(0, self.view.size()))
        MdeConvertBareLinkToMdLinkCommand.run(self, edit, name=name)
