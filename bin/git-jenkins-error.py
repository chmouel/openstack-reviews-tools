#!/usr/bin/env python
# -*- encoding: utf-8 -*-
#
# Simple launch gerrit-jenkins-error inside a git repo would get the failed
# job for the current commit (using the changeId) or you can add an
# argument be it the change-id the review number or even something
# like https://review.openstack.org/#/c/101010/ and it would do the
# right thing™
#
# Use the -t option to specify the output to something than /tmp
#
# Pro Tip: grep for failures and errors on the file:
#          egrep '(\[ERROR\]|FAIL)' /tmp/console*html
#          Get the python tracebacks :
#          sed -n '/Traceback/,/-----/ { p;}' /tmp/console*html

__author__ = "Chmouel Boudjnah <chmouel@chmouel.com>"

import argparse
import commands
from compiler.ast import flatten
import datetime
import itertools
import json
import operator
import re
import requests
import sys


def parse_review_failures(output):
    json_output = json.loads(output)
    # Only read the first line
    reg = re.compile(
        '^- ((gate|check)-[^ ]*).*(http://logs.openstack.org/[^ ]*).*FAILURE')
    failures = []

    if 'rowCount' in json_output:
        print("Seems like your change hasn't been tested yet.")
        sys.exit(1)

    for comment in json_output['comments']:
        if comment['reviewer']['username'] != 'jenkins':
            continue
        failures = []
        for line in re.split('\n', comment['message']):
            match = reg.match(line)
            if match:
                failures.append(match.group(1, 3))

    return failures


reg_zuul_log = re.compile('value="([^"]*)".*ZUUL_CHANGE_IDS.*')


def get_zuul_log_url(url):
    r = requests.get("%sparameters/" % url, verify=False)
    parts = reg_zuul_log.split(r.text)
    if parts:
        return "http://logs.openstack.org/%s" % parts[1]
    return None


def parse_zuul_failures(jobs):
    failures = []
    unfinished = []
    for job in jobs:
        if job['result'] == "FAILURE":
            failures.append((job['name'], get_zuul_log_url(job['url'])))
        elif job['result'] == "SKIPPED":
            continue
        else:
            unfinished.append((job['name'],
                               datetime.timedelta(job['remaining_time'])
                               if job['remaining_time'] != "0"
                               else "not started",
                               job['url']))
    return failures, unfinished


def inspect_zuul_head(head, change_id):
    for item in head:
        if isinstance(item, list):
            ret = inspect_zuul_head(item, change_id)
            if ret[0] is not None:
                return ret
        elif item['id'] == change_id:
            return parse_zuul_failures(item['jobs'])
    return None, None


def save_error(output_file, name,  url):
    if not url:
        print("* %s: No log found" % name)
    else:
        r = requests.get(url + "/console.html")
        try:
            if r.status_code == 404:
                r = requests.get(url + "/console.html.gz")
                r.raise_for_status()
        except(requests.exceptions.HTTPError), e:
            print("* %s: failed to download %s" % (name, url))
            print(e.message)
            return

        open(output_file, 'w').write(
            r.text.encode('ascii', 'ignore'))
        print("* %s: %s" % (name, output_file))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('-t', dest='tmpdir', default="/tmp",
                        help="Temporary Directory")
    parser.add_argument('change_id', nargs="?",
                        help="The ChangeID or review ID or review URL")
    args = parser.parse_args()

    if not args.change_id:
        status, output = commands.getstatusoutput(
            "git log --no-merges -n1|"
            "sed -n '/Change-Id: / { s/.*: //;p;}'")
        if status != 0 or not output.startswith('I'):
            print(output)
            sys.exit(1)
        change_id = output
    elif args.change_id.startswith("https://review.openstack.org/#/c/"):
        change_id = args.change_id.replace(
            "https://review.openstack.org/#/c/", '').replace('/', '')
    else:
        change_id = args.change_id

    r = requests.get('http://zuul.openstack.org/status.json')
    if r.status_code != 200:
        print("Zuul request failed: ")
        print(r.text)
        sys.exit(1)

    data = r.json()

    failures = None
    unfinished = None
    merged_queues = itertools.chain.from_iterable(
        itertools.imap(operator.itemgetter('change_queues'),
                       data['pipelines']))
    changes = flatten(itertools.chain.from_iterable(
        itertools.imap(operator.itemgetter('heads'), merged_queues)))

    for change in changes:
        if change['id'] == change_id:
            failures, unfinished = parse_zuul_failures(change['jobs'])

    if failures is None:  # change_id not found in zuul
        status, output = commands.getstatusoutput(
            "ssh -x -p 29418 review.openstack.org "
            "'gerrit query --format=JSON --comments --current-patch-set "
            "change: %s'" % change_id)
        if status != 0:
            print(output)
            sys.exit(1)

        output = output.split('\n')
        if len(output) == 1:
            print("Seems like an invalid change")
            sys.exit(1)

        failures = parse_review_failures(output[0])

    if unfinished:
        print("Unfinished Jobs: ")
        for (name, remaining_time, url) in unfinished:
            print(" - %s (%ss): %s" % (name, remaining_time, url))
    if not failures:
        print("No failures with Jenkins!! Yay!")
        return

    print("Failed Jobs: ")
    for (name, url) in failures:
        save_error("%s/%s-%s.html" % (
            args.tmpdir, name, change_id[1:6]), name, url)


if __name__ == '__main__':
    main()
