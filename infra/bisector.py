# Copyright 2019 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Uses bisection to determine which commit a bug was introduced and fixed.
This module takes a high and a low commit SHA, a repo name, and a bug.
The module bisects the high and low commit SHA searching for the location
where the bug was introduced. It also looks for where the bug was solved.
This is done with the following steps:


  NOTE: NEEDS TO BE RUN FROM THE oss-fuzz HOME directory

  Typical usage example:
    1. (Host) Clone the main project repo on the host
    2. (Host) Run git fetch --unshallow
    3. (Host) Use git bisect to identify the next commit to check
    4. (Host) Build the image at the specific commit
    5. (Host) Mount the repo with the correct commit over the build image repo
    5. (Host) Build the fuzzers from new image with updated repo
    6. (Host) Test for bug’s existence
    7. Go to step 3
    python bisect.py --project_name curl
      --commit_new dda418266c99ceab368d723facb52069cbb9c8d5
      s
      --fuzzer_name curl_fuzzer_ftp
      --test_case /usr/local/google/home/lneat/Downloads/clusterfuzz-test\
          case-minimized-curl_fuzzer_ftp-5657400807260160
"""

import argparse

from helper import build_fuzzers
from helper import reproduce
from build_specified_commit import build_fuzzer_from_commit
from build_specified_commit import infer_main_repo
from RepoManager import RepoManager


def main():
  parser = argparse.ArgumentParser(
      'bisector.py',
      description='git bisection for finding introduction of bugs')

  parser.add_argument(
      '--project_name',
      help='The name of the project where the bug occured',
      required=True)
  parser.add_argument(
      '--commit_new',
      help='The newest commit SHA to be bisected',
      required=True)
  parser.add_argument(
      '--commit_old',
      help='The oldest commit SHA to be bisected',
      required=True)
  parser.add_argument(
      '--fuzzer_name', help='the name of the fuzzer to be built', required=True)
  parser.add_argument(
      '--test_case', help='the test_case to be reproduced', required=True)
  parser.add_argument('--engine', default='libfuzzer')
  parser.add_argument(
      '--sanitizer',
      default='address',
      help='the default is "address"; "dataflow" for "dataflow" engine')
  parser.add_argument('--architecture', default='x86_64')
  args = parser.parse_args()

  error_sha = init_bisection(args.project_name, args.commit_old,
                             args.commit_new, args.engine, args.sanitizer,
                             args.architecture, args.test_case,
                             args.fuzzer_name)
  if not error_sha:
    print('No error was found in commit range %s:%s' %
          (args.commit_old, args.commit_new))
  else:
    print('Error was introduced at commit %s' % error_sha)


def init_bisection(project_name, commit_old, commit_new, engine, sanitizer,
                   architecture, test_case, fuzzer_name):

  LOCAL_STORE_PATH = 'tmp'
  repo_url = infer_main_repo(project_name, LOCAL_STORE_PATH, commit_old)
  rm = RepoManager(repo_url, LOCAL_STORE_PATH)
  commit_list = rm.get_commit_list(commit_old, commit_new)

  # Handle the case where there is only one SHA passed in
  if len(commit_list) != 1:
    build_fuzzer_from_commit(project_name, commit_list[0], rm.repo_dir, engine,
                           sanitizer, architecture, rm)
    error_code = reproduce_error(project_name, test_case, fuzzer_name)
  else:
    error_code = None
  index = bisection(project_name, 0,
                    len(commit_list) - 1, commit_list, rm, len(commit_list),
                    error_code, engine, sanitizer, architecture, test_case,
                    fuzzer_name)
  if index is not None:
    return commit_list[index]
  else:
    return -1


def bisection(project_name, commit_new_idx, commit_old_idx, commit_list,
              repo_manager, last_error, error_code, engine, sanitizer,
              architecture, test_case, fuzzer_name):
  """Returns the commit ID where a bug was introduced.

  Args:
    commit_old_idx: The oldest commit SHA index in the search space
    commit_new_idx: The newest commit SHA index in the search space
    commit_list: The list of all commit SHAs
    repo_manager: The class handling all of the git repo calls
    last_error: The index where the last error was found

  Returns:
    The index of the SHA string where the bug was introduced
  """
  cur_idx = (commit_new_idx + commit_old_idx) // 2
  print("Commit list: \n %s" % commit_list)
  print("Current index: %s" % str(cur_idx))
  print("High index: %s low index %s" % (str(commit_new_idx), str(commit_old_idx)))
  build_fuzzer_from_commit(project_name, commit_list[cur_idx],
                           repo_manager.repo_dir, engine, sanitizer,
                           architecture,repo_manager)
  new_error_code = reproduce_error(project_name, test_case, fuzzer_name)
  if new_error_code == error_code:
    error_exists = True
  else:
    error_exists = False

  if commit_new_idx == commit_old_idx:
    if error_exists:
      return cur_idx
    else:
      return last_error

  if error_exists:
    return bisection(project_name, cur_idx + 1, commit_old_idx, commit_list,
                     repo_manager, cur_idx, error_code, engine, sanitizer,
                     architecture, test_case, fuzzer_name)
  else:
    if cur_idx == 0:
      return None
    return bisection(project_name, commit_new_idx, cur_idx - 1, commit_list,
                     repo_manager, last_error, error_code, engine, sanitizer,
                     architecture, test_case, fuzzer_name)


def reproduce_error(project_name, test_case, fuzzer_name):
  """Checks to see if the error is repoduceable at a specific commit.
  Args:
    project_name: The name of the project you are testing
    test_case: The path to the test_case you are passing in
    fuzzer_name: The name of the fuzz target to be tested
  Returns:
    True if the error still exists
  """
  parser = argparse.ArgumentParser()
  parser.add_argument('project_name', help='name of the project')
  parser.add_argument('fuzzer_name', help='name of the fuzzer')
  parser.add_argument('testcase_path', help='path of local testcase')
  parser.add_argument(
      'fuzzer_args',
      help='arguments to pass to the fuzzer',
      nargs=argparse.REMAINDER)
  parser.add_argument(
      '--valgrind', action='store_true', help='run with valgrind')
  parser.add_argument(
      '-e', action='append', help='set environment variable e.g. VAR=value')
  args = parser.parse_args([project_name, fuzzer_name, test_case])
  return reproduce(args)


if __name__ == '__main__':
  main()
