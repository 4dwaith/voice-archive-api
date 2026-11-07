Feature: Prepare English summaries for a published book
  Summaries become visible together through the normal book and content reads.
  Failed replacements preserve the summaries readers already have.

  Background:
    Given a published Kannada book is ready for summarization
    And the summary provider is available

  Scenario: Generate and publish summaries
    When I generate summaries with the local command
    Then English summaries are available for the book, sections and passages
    And the previous content version is rejected by paginated reads
    And generation uses the complete canonical source outside publication locks

  Scenario: Repeating an unchanged book avoids provider work
    Given summaries have already been generated
    When I generate summaries with the local command
    Then the command skips generation and preserves the published version

  Scenario: English aliases do not request passage translations
    Given the book declares the English alias eng
    When I generate summaries with the local command
    Then English summaries are available for the book, sections and passages
    And the provider receives resolved English and provenance keeps the declared alias

  Scenario: An unresolved book language prevents generation
    Given the book declares the unresolved language "und"
    When I generate summaries with the local command
    Then the command requests a primary language before any provider work or run is created

  Scenario: Clear accepted summaries
    Given summaries have already been generated
    When I clear summaries with the local command
    Then all summary reads are empty under a new content version
    And the previous content version is rejected by paginated reads

  Scenario: A failed replacement preserves accepted summaries
    Given summaries have already been generated
    And the book title changes before a rejected replacement
    When I generate summaries with the local command
    Then the command reports failure and preserves the accepted summary reads

  Scenario: A missing passage translation prevents replacement
    Given summaries have already been generated
    And the book title changes before a replacement without its passage translation
    When I generate summaries with the local command
    Then the command reports failure and preserves the accepted summary reads

  Scenario: Publish three root sections in order
    Given the book has three root sections
    When I generate each root section in order with the local command
    Then each root is published under its own version with earlier context

  Scenario: Earlier root summaries are required
    Given the book has three root sections
    When I generate the second root section with the local command
    Then the command reports missing earlier roots without contacting the provider

  Scenario: Later summaries prevent generation
    Given the book has three root sections
    And the last root already has a summary
    When I generate the first root section with the local command
    Then the command reports later summaries without contacting the provider

  Scenario: Explicitly accept stale later summaries
    Given the book has three root sections
    And the last root already has a summary
    When I generate the first root section allowing stale downstream summaries
    Then the first root is published and the later summary is preserved
