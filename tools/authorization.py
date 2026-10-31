#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Authorize CI requests using organization membership and trusted ownership."""

import base64
from functools import lru_cache
import re
import subprocess

from configure import OWNER_ID, OWNER_LOGIN
from workspace import ORG, SHA, api


def require_org_member(actor):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9-]*', actor):
        raise ValueError('System CI requires an authenticated organization member')
    try:
        member = api(f'orgs/{ORG}/memberships/{actor}')
    except subprocess.CalledProcessError as error:
        raise ValueError(f'{actor}: active {ORG} membership could not be verified') from error
    if (member.get('state') != 'active'
            or member.get('organization', {}).get('login') != ORG
            or member.get('user', {}).get('login', '').lower() != actor.lower()):
        raise ValueError(f'{actor}: only active {ORG} members may request system CI')
    return member['user']


def require_submitter(actor):
    # A repository admin or outside collaborator cannot proxy an unauthorized
    # merge through the App. The sole human merge authority is explicit.
    if actor.lower() != OWNER_LOGIN:
        raise ValueError(f'Only CODEOWNER {OWNER_LOGIN} may request topic submission')
    member = require_org_member(actor)
    if member['id'] != OWNER_ID:
        raise ValueError('CODEOWNER account identity changed')


@lru_cache(maxsize=512)
def trusted_ownership(repository, revision):
    if not SHA.fullmatch(revision):
        raise ValueError('Ownership must be read from an exact trusted main revision')
    source = api(f'repos/{ORG}/{repository}/contents/.github/CODEOWNERS?ref={revision}')
    if source.get('encoding') != 'base64' or source.get('type') != 'file':
        raise ValueError(f'{repository}: trusted CODEOWNERS is not a regular file')
    text = base64.b64decode(source['content'], validate=False).decode('utf-8')
    records = [line.split('#', 1)[0].split() for line in text.splitlines()]
    records = [line for line in records if line]
    owner = '@' + OWNER_LOGIN
    # Current organization policy assigns the whole repository to roolrz.
    # Fail closed on ownership removal or path overrides that exclude them;
    # proposed CODEOWNERS changes never grant submission permission.
    if (not any(line[0] == '*' and owner in line[1:] for line in records)
            or any(owner not in line[1:] for line in records)):
        raise ValueError(f'{repository}: {OWNER_LOGIN} must own the entire trusted repository')


def require_codeowner(repository, actor, revision=None):
    require_submitter(actor)
    if revision is None:
        revision = api(f'repos/{ORG}/{repository}/commits/main')['sha']
    trusted_ownership(repository, revision)
