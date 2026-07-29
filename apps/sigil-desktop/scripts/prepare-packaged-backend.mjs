#!/usr/bin/env node

import fs from 'node:fs'
import path from 'node:path'
import process from 'node:process'

const appDirectory = path.resolve(import.meta.dirname, '..')
const sourcePackage = path.resolve(appDirectory, '../sigil/src/sigil')
const initializer = path.join(appDirectory, 'packaged-backend/sigil/__init__.py')
const stagingRoot = path.join(appDirectory, 'packaged-backend/staged')
const temporaryRoot = path.join(
  appDirectory,
  `packaged-backend/.staged-${process.pid}`
)

function copyPythonTree(source, destination) {
  fs.mkdirSync(destination, { recursive: true })

  for (const entry of fs.readdirSync(source, { withFileTypes: true })
    .sort((left, right) => left.name.localeCompare(right.name))) {
    const sourcePath = path.join(source, entry.name)
    const destinationPath = path.join(destination, entry.name)

    if (entry.isDirectory()) {
      if (entry.name !== '__pycache__') {
        copyPythonTree(sourcePath, destinationPath)
      }
    } else if (entry.isFile() && entry.name.endsWith('.py')) {
      fs.copyFileSync(sourcePath, destinationPath, fs.constants.COPYFILE_EXCL)
    }
  }
}

if (!fs.statSync(sourcePackage).isDirectory()) {
  throw new Error(`Sigil backend source directory not found: ${sourcePackage}`)
}

fs.rmSync(temporaryRoot, { recursive: true, force: true })

try {
  const temporaryPackage = path.join(temporaryRoot, 'sigil')
  copyPythonTree(sourcePackage, temporaryPackage)
  fs.copyFileSync(initializer, path.join(temporaryPackage, '__init__.py'))

  fs.rmSync(stagingRoot, { recursive: true, force: true })
  fs.renameSync(temporaryRoot, stagingRoot)
} catch (error) {
  fs.rmSync(temporaryRoot, { recursive: true, force: true })
  throw error
}

const pythonFiles = []
function collectPythonFiles(directory) {
  for (const entry of fs.readdirSync(directory, { withFileTypes: true })
    .sort((left, right) => left.name.localeCompare(right.name))) {
    const entryPath = path.join(directory, entry.name)
    if (entry.isDirectory()) {
      collectPythonFiles(entryPath)
    } else if (entry.isFile() && entry.name.endsWith('.py')) {
      pythonFiles.push(entryPath)
    }
  }
}
collectPythonFiles(path.join(stagingRoot, 'sigil'))

process.stdout.write(
  `Prepared ${pythonFiles.length} Python files in ${stagingRoot}\n`
)
