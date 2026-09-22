# Repository Coverage

[Full report](https://htmlpreview.github.io/?https://github.com/dbulnes/indigo-stats/blob/python-coverage-comment-action-data/htmlcov/index.html)

| Name                                     |    Stmts |     Miss |   Cover |   Missing |
|----------------------------------------- | -------: | -------: | ------: | --------: |
| backend/\_\_init\_\_.py                  |        0 |        0 |    100% |           |
| backend/air.py                           |       76 |        0 |    100% |           |
| backend/app.py                           |      210 |        1 |     99% |        82 |
| backend/backups.py                       |      734 |        0 |    100% |           |
| backend/config.py                        |       52 |        0 |    100% |           |
| backend/db.py                            |       80 |        0 |    100% |           |
| backend/jobs.py                          |       99 |        0 |    100% |           |
| backend/manage.py                        |       38 |        0 |    100% |           |
| backend/tests/test\_backup\_api.py       |      166 |        0 |    100% |           |
| backend/tests/test\_backup\_hardening.py |      231 |        0 |    100% |           |
| backend/tests/test\_backups.py           |      528 |        0 |    100% |           |
| backend/tests/test\_core.py              |      322 |        0 |    100% |           |
| backend/tests/test\_jobs.py              |       94 |        0 |    100% |           |
| backend/tests/test\_manage.py            |       69 |        0 |    100% |           |
| **TOTAL**                                | **2699** |    **1** | **99%** |           |


## Setup coverage badge

Below are examples of the badges you can use in your main branch `README` file.

### Direct image

[![Coverage badge](https://raw.githubusercontent.com/dbulnes/indigo-stats/python-coverage-comment-action-data/badge.svg)](https://htmlpreview.github.io/?https://github.com/dbulnes/indigo-stats/blob/python-coverage-comment-action-data/htmlcov/index.html)

This is the one to use if your repository is private or if you don't want to customize anything.

### [Shields.io](https://shields.io) Json Endpoint

[![Coverage badge](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/dbulnes/indigo-stats/python-coverage-comment-action-data/endpoint.json)](https://htmlpreview.github.io/?https://github.com/dbulnes/indigo-stats/blob/python-coverage-comment-action-data/htmlcov/index.html)

Using this one will allow you to [customize](https://shields.io/endpoint) the look of your badge.
It won't work with private repositories. It won't be refreshed more than once per five minutes.

### [Shields.io](https://shields.io) Dynamic Badge

[![Coverage badge](https://img.shields.io/badge/dynamic/json?color=brightgreen&label=coverage&query=%24.message&url=https%3A%2F%2Fraw.githubusercontent.com%2Fdbulnes%2Findigo-stats%2Fpython-coverage-comment-action-data%2Fendpoint.json)](https://htmlpreview.github.io/?https://github.com/dbulnes/indigo-stats/blob/python-coverage-comment-action-data/htmlcov/index.html)

This one will always be the same color. It won't work for private repos. I'm not even sure why we included it.

## What is that?

This branch is part of the
[python-coverage-comment-action](https://github.com/marketplace/actions/python-coverage-comment)
GitHub Action. All the files in this branch are automatically generated and may be
overwritten at any moment.