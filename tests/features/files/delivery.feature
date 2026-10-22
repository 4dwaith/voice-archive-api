Feature: Retrieve prepared book files
  Prepared files can be previewed before linking them to a book.
  PDF clients can retrieve byte ranges without downloading the complete file.

  Scenario Outline: Retrieve a prepared file before saving a book
    Given I have uploaded a prepared <kind>
    When I retrieve the prepared file
    Then the complete prepared file is returned
    And delivery headers identify the <kind>

    Examples:
      | kind       |
      | cover      |
      | source PDF |

  Scenario Outline: Inspect a file without downloading its content
    Given I have uploaded a prepared <kind>
    When I request the prepared file headers
    Then the headers describe the complete file without a body

    Examples:
      | kind       |
      | cover      |
      | source PDF |

  # Positions are inclusive. Negative positions count back from the end;
  # "last" means the final byte. Expected positions below are absolute.
  Scenario Outline: Retrieve selected PDF bytes
    Given I have uploaded a prepared source PDF of <size> bytes
    When I request bytes from <start> through <end>
    Then only stored bytes <first> through <final> are returned

    Examples:
      | size   | start | end   | first | final |
      | 131072 | 65535 | 65537 | 65535 | 65537 |
      | 1024   | 100   | last  | 100   | 1023  |
      | 1024   | -20   | last  | 1004  | 1023  |

  Scenario: Report a range beyond the available PDF bytes
    Given I have uploaded a prepared source PDF of 1024 bytes
    When I request a range starting beyond the prepared file
    Then I am told that the requested range is outside the file
    And the range error identifies the complete file size

  Scenario: Report a range whose end precedes its start
    Given I have uploaded a prepared source PDF of 1024 bytes
    When I request bytes from 100 through 99
    Then I am told that the requested range is invalid

  Scenario Outline: A missing file cannot be retrieved
    Given the prepared <kind> is missing
    When I retrieve the prepared file
    Then I am told that the <kind> cannot be found

    Examples:
      | kind       |
      | cover      |
      | source PDF |

  Scenario Outline: Report a missing stored file using the file's own error
    Given I have uploaded a prepared <kind>
    And the stored file has been removed
    When I retrieve the prepared file
    Then I am told that the <kind> cannot be found

    Examples:
      | kind       |
      | cover      |
      | source PDF |
