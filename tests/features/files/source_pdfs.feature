Feature: Upload a source PDF independently of a book
  Valid unencrypted PDFs retain their original bytes. Uploading does not create
  a book or change its links; linking requires a separate book create/save.

  Background:
    Given I am uploading a source PDF

  Scenario: Prepare a native PDF and record its page count
    Given I have selected a PDF with 3 pages
    When I upload the file
    Then the PDF is ready to link to a book
    And the PDF has 3 pages
    And no book has been created

  Scenario: Prepare an image-based PDF
    Given I have selected a scanned PDF
    When I upload the file
    Then the PDF is ready to link to a book

  Scenario: Determine PDF validity from its content rather than its name or MIME claim
    Given I have selected a PDF with 3 pages
    And the filename is "../../misleading.png" and the declared MIME type is "image/png"
    When I upload the file
    Then the PDF is ready to link to a book

  Scenario Outline: Reject invalid PDFs
    Given I have selected a file with <sample> content
    When I upload the file
    Then the upload fails with status 422 and code "invalid_pdf"
    And no file has been prepared

    Examples:
      | sample  |
      | empty   |
      | corrupt |

  Scenario: Reject a PDF without pages
    Given I have selected a PDF with 0 pages
    When I upload the file
    Then the upload fails with status 422 and code "invalid_pdf"
    And no file has been prepared

  Scenario Outline: Reject encrypted PDFs even when the password is empty
    Given I have selected an encrypted PDF with password "<password>"
    When I upload the file
    Then the upload fails with status 422 and code "encrypted_pdf_not_supported"
    And no file has been prepared

    Examples:
      | password |
      | secret   |
      |          |

  Scenario: Accept a PDF exactly at its configured input limit
    Given I have selected a PDF with 3 pages
    And the file size equals the configured limit
    When I upload the file
    Then the PDF is ready to link to a book

  Scenario: Reject a PDF one byte over its configured input limit
    Given I have selected a PDF with 3 pages
    And the file size is one byte above the configured limit
    When I upload the file
    Then the upload fails with status 413 and code "file_too_large"
    And no file has been prepared

