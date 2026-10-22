Feature: Import prepared book content
  A prepared dataset is published once as a whole. The API validates its text,
  hierarchy, PDF regions, and narration against exact uploaded files.

  Background:
    Given a book and valid prepared text, PDF, and chapter audio

  Scenario: Publish structured text and claim prepared media together
    When I import the prepared content
    Then I was told the content was imported successfully
    And the complete ordered dataset and its formatting are stored
    And the PDF and each chapter audio belong to their intended owners
    And chapter audio can be read through byte-range delivery

  Scenario: Dry-run validates without publishing or reserving files
    When I dry-run the prepared content
    Then I was told the prepared content is valid without importing it
    And the book and prepared files are unchanged
    When I import the prepared content
    Then I was told the content was imported successfully
    And the complete ordered dataset and its formatting are stored

  Scenario: Import using the PDF already attached to this book
    Given the prepared PDF is already attached to the book
    When I import the prepared content
    Then I was told the content was imported successfully
    And the PDF and each chapter audio belong to their intended owners

  Scenario: Text-only content needs no PDF or narration
    Given the prepared content is text-only
    When I import the prepared content
    Then I was told the content was imported successfully
    And the complete ordered dataset and its formatting are stored
    And the unused prepared media remain unlinked

  Scenario Outline: Existing content rejects another publication or dry-run
    Given the prepared content has already been imported
    When I <operation> the prepared content
    Then I was told the book already has imported content
    And the book and prepared files are unchanged

    Examples:
      | operation |
      | import    |
      | dry-run   |

  Scenario Outline: Invalid input never publishes partial content or claims files
    Given the prepared input has <problem>
    When I <operation> the prepared content
    Then I was told <reason>
    And the book and prepared files are unchanged

    Examples:
      | operation | problem                         | reason                                                 |
      | import    | unsupported formatting          | the formatting is unsupported                          |
      | dry-run   | unsupported formatting          | the formatting is unsupported                          |
      | import    | a missing section reference     | a referenced section is missing                        |
      | import    | cyclic sections                 | the sections form a cycle                              |
      | import    | a PDF region outside the book   | a PDF region falls outside the book                    |
      | dry-run   | a PDF region outside the book   | a PDF region falls outside the book                    |
      | import    | a missing audio file            | a referenced audio file could not be found             |
      | import    | a shared audio file             | the same audio file cannot belong to multiple chapters |
      | import    | the wrong file kind             | a referenced file has the wrong kind                   |
      | import    | a PDF belonging to another book | the PDF belongs to another book                        |
      | import    | narration beyond the audio      | the narration extends beyond the audio                 |
      | import    | nested audio without root audio | nested narration requires root chapter audio           |
      | import    | an unknown segment kind         | the segment kind is unsupported                        |

  Scenario: An import cannot substitute a different attached PDF
    Given the prepared PDF is already attached to the book
    And the prepared content refers to another PDF
    When I import the prepared content
    Then I was told the content refers to a different PDF than the one attached to the book
    And the book and prepared files are unchanged

  Scenario: Import requires an existing book
    When I import the prepared content into a missing book
    Then I was told the book could not be found
    And the book and prepared files are unchanged

  Scenario: Simultaneous imports publish exactly one complete dataset
    When two requests import the prepared content at the same time
    Then one import succeeds and the other reports existing content
    And the complete ordered dataset and its formatting are stored
    And the PDF and each chapter audio belong to their intended owners

  Scenario Outline: Imported chapter audio cannot be deleted independently
    Given the prepared content has already been imported
    When I delete the linked <section> chapter audio
    Then I was told the chapter audio is still in use
    And the book and prepared files are unchanged

    Examples:
      | section |
      | root    |
      | nested  |
