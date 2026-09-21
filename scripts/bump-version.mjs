#!/usr/bin/env node

import { readFile, writeFile } from 'node:fs/promises'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const packagePath = resolve(root, 'web/package.json')
const lockPath = resolve(root, 'web/package-lock.json')
const backendPath = resolve(root, 'backend/app.py')
const semverPattern = /^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$/
const backendPattern = /^APP_VERSION\s*=\s*(['"])([^'"]+)\1\s*$/m

function fail(message) {
  console.error(`Version update failed: ${message}`)
  process.exit(1)
}

function increment(version, part) {
  if (version.includes('-') || version.includes('+')) {
    fail(`use an explicit version when the current version has a prerelease or build suffix (${version})`)
  }

  const values = version.split('.').map(Number)
  const index = { major: 0, minor: 1, patch: 2 }[part]
  values[index] += 1
  for (let current = index + 1; current < values.length; current += 1) values[current] = 0
  return values.join('.')
}

const argument = process.argv[2]
if (!argument || process.argv.length > 3) {
  fail('usage: node scripts/bump-version.mjs <major|minor|patch|X.Y.Z|--check>')
}

const [packageText, lockText, backendText] = await Promise.all([
  readFile(packagePath, 'utf8'),
  readFile(lockPath, 'utf8'),
  readFile(backendPath, 'utf8'),
])

const packageJson = JSON.parse(packageText)
const lockJson = JSON.parse(lockText)
const backendMatches = [...backendText.matchAll(new RegExp(backendPattern.source, 'gm'))]
if (backendMatches.length !== 1) fail('backend/app.py must contain exactly one APP_VERSION assignment')
const backendMatch = backendMatches[0]

const versions = new Map([
  ['web/package.json', packageJson.version],
  ['web/package-lock.json', lockJson.version],
  ['web/package-lock.json root package', lockJson.packages?.['']?.version],
  ['backend/app.py', backendMatch[2]],
])
const currentVersions = new Set(versions.values())
if (currentVersions.size !== 1 || currentVersions.has(undefined)) {
  fail(`existing versions are out of sync:\n${[...versions].map(([file, version]) => `  ${file}: ${version ?? 'missing'}`).join('\n')}`)
}

const currentVersion = packageJson.version
if (!semverPattern.test(currentVersion)) fail(`current version is not valid semantic versioning: ${currentVersion}`)

if (argument === '--check') {
  console.log(`Application version ${currentVersion} is synchronized`)
  process.exit(0)
}

const nextVersion = ['major', 'minor', 'patch'].includes(argument)
  ? increment(currentVersion, argument)
  : argument
if (!semverPattern.test(nextVersion)) fail(`invalid semantic version: ${nextVersion}`)

if (nextVersion === currentVersion) {
  console.log(`Application version is already ${currentVersion}`)
  process.exit(0)
}

packageJson.version = nextVersion
lockJson.version = nextVersion
lockJson.packages[''].version = nextVersion
const nextBackendText = backendText.replace(
  backendPattern,
  `APP_VERSION = '${nextVersion}'`,
)

await Promise.all([
  writeFile(packagePath, `${JSON.stringify(packageJson, null, 2)}\n`),
  writeFile(lockPath, `${JSON.stringify(lockJson, null, 2)}\n`),
  writeFile(backendPath, nextBackendText),
])

console.log(`Application version ${currentVersion} -> ${nextVersion}`)
