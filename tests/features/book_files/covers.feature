Feature: Upload a cover independently of a book
  Covers are decoded from their contents, normalized to WebP, and made ready
  before their returned IDs can be linked through a book save.

  Background:
    Given I am uploading a cover

  Scenario Outline: Prepare an accepted image format
    Given I have selected a <format> image
    When I upload the file
    Then the cover is ready to link to a book
    And no book has been created

    Examples:
      | format |
      | JPEG   |
      | PNG    |
      | WEBP   |

  Scenario: Determine image format from content rather than its name or MIME claim
    Given I have selected a PNG image
    And the filename is "../../misleading.pdf" and the declared MIME type is "application/pdf"
    When I upload the file
    Then the cover is ready to link to a book

  Scenario Outline: Reject invalid images
    Given I have selected a file with <sample> content
    When I upload the file
    Then the upload fails with status 422 and code "invalid_image"
    And no file has been prepared

    Examples:
      | sample  |
      | empty   |
      | corrupt |

  Scenario: Reject a recognizable unsupported image format
    Given I have selected a GIF image
    When I upload the file
    Then the upload fails with status 415 and code "unsupported_media_type"
    And no file has been prepared

  Scenario: Accept a cover exactly at its configured input limit
    Given I have selected a PNG image
    And the file size equals the configured limit
    When I upload the file
    Then the cover is ready to link to a book

  Scenario: Reject a cover one byte over its configured input limit
    Given I have selected a PNG image
    And the file size is one byte above the configured limit
    When I upload the file
    Then the upload fails with status 413 and code "file_too_large"
    And no file has been prepared

