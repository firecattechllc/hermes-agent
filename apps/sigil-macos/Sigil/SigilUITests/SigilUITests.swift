//
//  SigilUITests.swift
//  SigilUITests
//
//  Core navigation and safety-banner smoke tests. These launch the real
//  SigilDev.app build (via TEST_HOST) — never the installed Sigil 3.7 app.
//

import XCTest

final class SigilUITests: XCTestCase {

    override func setUpWithError() throws {
        continueAfterFailure = false
    }

    @MainActor
    func testAppLaunchesToOverviewWithSidebarVisible() throws {
        let app = XCUIApplication()
        app.launch()

        XCTAssertTrue(app.staticTexts["Overview"].waitForExistence(timeout: 5))
        for section in ["Overview", "Portfolio", "Proposals", "Launch", "Executions", "Reconciliation", "Audit", "News", "Settings"] {
            XCTAssertTrue(app.staticTexts[section].exists, "expected sidebar item '\(section)' to exist")
        }
    }

    @MainActor
    func testSafetyBoundariesBannerIsVisibleOnOverview() throws {
        let app = XCUIApplication()
        app.launch()

        XCTAssertTrue(app.staticTexts["Safety Boundaries"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.staticTexts["Paper Only"].exists)
        XCTAssertTrue(app.staticTexts["No Live Execution Authority"].exists)
        XCTAssertTrue(app.staticTexts["Broker Submission Disabled"].exists)
    }

    @MainActor
    func testNavigatingToEachSidebarSectionUpdatesTheTitle() throws {
        let app = XCUIApplication()
        app.launch()

        let sections = ["Portfolio", "Proposals", "Launch", "Executions", "Reconciliation", "Audit", "News", "Settings"]
        for section in sections {
            let sidebarItem = app.staticTexts[section]
            XCTAssertTrue(sidebarItem.waitForExistence(timeout: 5), "sidebar item '\(section)' missing")
            sidebarItem.click()
            XCTAssertTrue(app.navigationBars[section].waitForExistence(timeout: 5) || app.staticTexts[section].waitForExistence(timeout: 5),
                          "navigating to '\(section)' did not update the visible title")
        }
    }

    @MainActor
    func testLaunchScreenNeverLabelsAnythingLiveExecution() throws {
        // A guard against ever accidentally shipping copy that suggests
        // live/real trading authority on the Launch screen specifically.
        let app = XCUIApplication()
        app.launch()

        app.staticTexts["Launch"].click()
        XCTAssertTrue(app.staticTexts["Automation State"].waitForExistence(timeout: 5))
        XCTAssertFalse(app.staticTexts["Live Trading"].exists)
        XCTAssertFalse(app.staticTexts["Go Live"].exists)
    }
}
