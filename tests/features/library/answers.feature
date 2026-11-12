Feature: Answer questions from prepared canonical passages
  Answers use prepared passages from the selected scope and provide passages that can
  be followed in the reader. Unavailable or changed evidence never becomes an answer.

  Background:
    Given my library contains a published book about a journey
    And the answer service is available

  Scenario: Ask a question and receive supporting passages
    When I ask why the traveller returned home
    Then the answer explains the return with its supporting passage
    And the answer service receives the question and the selected ordered passages

  Scenario: Reranking uses contextual summaries and retains original neighboring passages
    Given the journey book has prepared passage search
    And the reranker returns valid scores
    When I ask why the traveller returned home
    Then the answer explains the return with its supporting passage
    And contextual reranking preserves original answer evidence

  Scenario Outline: Reranking failures preserve hybrid answering
    Given the journey book has prepared passage search
    And the reranker returns <failure>
    When I ask why the traveller returned home
    Then the answer explains the return with its supporting passage
    And reranking failure diagnostics exclude passage content

    Examples:
      | failure        |
      | a rejection    |
      | invalid scores |
      | a timeout      |

  Scenario: Source changes during reranking invalidate the answer
    Given the journey book has prepared passage search
    And the reranker returns a source change
    When I ask why the traveller returned home
    Then the source change completes and I am told to ask again

  Scenario: The book does not establish an answer
    Given the answer service finds no evidence for the question
    When I ask why the traveller returned home
    Then I am told that the book does not establish an answer

  Scenario Outline: Request a reasoning effort for the question
    Given I want <effort> reasoning effort
    When I ask why the traveller returned home
    Then the answer explains the return with its supporting passage
    And the answer service uses the requested reasoning effort

    Examples:
      | effort |
      | low    |
      | medium |
      | high   |

  Scenario Outline: An unusable answer is not delivered
    Given the answer service encounters <problem>
    When I ask why the traveller returned home
    Then I am told that an answer is temporarily unavailable

    Examples:
      | problem                       |
      | a network failure             |
      | a timeout                     |
      | a provider rejection          |
      | malformed output              |
      | a blocked response            |
      | a truncated response          |
      | an unknown passage            |
      | a mismatched passage reference |
      | a passage from another book   |
      | repeated passages             |
      | an answer without support     |
      | an empty answer               |
      | an unexplained passage        |
      | missing model limits          |
      | missing token count           |

  Scenario Outline: Selected evidence must fit before it is submitted for answering
    Given the complete request exceeds the <limit> input limit
    When I ask why the traveller returned home
    Then I am told the selected evidence is too large without submitting an answer

    Examples:
      | limit      |
      | provider   |
      | configured |

  Scenario Outline: Missing source is explicit
    Given the selected book is <state>
    When I ask why the traveller returned home
    Then I am told the selected source is <availability>

    Examples:
      | state                  | availability |
      | not imported           | unavailable  |
      | removed                | missing      |
      | published without text | unavailable  |

  Scenario Outline: A slow answer does not prevent source changes
    Given the book is <change> while the answer is being prepared
    When I ask why the traveller returned home
    Then the source change completes and I am told to ask again

    Examples:
      | change   |
      | purged   |
      | replaced |
      | deleted  |

  Scenario: Answering needs server configuration
    Given the answer service has no credential
    When I ask why the traveller returned home
    Then I am told that an answer is temporarily unavailable

  Scenario: An English question retrieves Kannada passages inside the selected book
    Given the journey book has prepared passage search
    And an unrelated prepared book is also in the library
    When I ask why the traveller returned home
    Then only selected canonical passages are read and supplied

  Scenario: Kannada wording searches both source representations
    Given the journey book has prepared passage search
    And the question is in Kannada with English wording
    When I ask why the traveller returned home
    Then only selected canonical passages are read and supplied

  Scenario: English books use only their English representation
    Given the journey book is declared English
    And the journey book has prepared passage search
    And the question is in Kannada with English wording
    When I ask why the traveller returned home
    Then only selected canonical passages are read and supplied

  Scenario: Weak retrieved evidence retains uncertainty without loading the whole book
    Given the journey book has prepared passage search
    And the answer service finds no evidence for the question
    When I ask why the traveller returned home
    Then only selected canonical passages are read and supplied
    And I am told that the book does not establish an answer

  Scenario Outline: Broad scope excludes unprepared member source
    Given the journey book has prepared passage search
    And I ask across the <scope> with an unprepared member book
    When I ask why the traveller returned home
    Then only selected canonical passages are read and supplied
    And the unprepared member source is excluded

    Examples:
      | scope   |
      | series  |
      | library |

  Scenario: Series retrieval excludes unrelated prepared source
    Given the journey book has prepared passage search
    And I ask across the series with an unprepared member book
    And an unrelated prepared book is also in the library
    When I ask why the traveller returned home
    Then only selected canonical passages are read and supplied
    And the unprepared member source is excluded

  Scenario Outline: Unavailable preparation fails a book query
    Given the journey book has prepared passage search
    And the prepared search is <state>
    When I ask why the traveller returned home
    Then I am told that an answer is temporarily unavailable
    And no canonical source or answer generation is loaded

    Examples:
      | state                             |
      | missing windows                   |
      | missing embeddings                |
      | incomplete                        |
      | incompatible                      |
      | zero magnitude                    |
      | cleared while embedding the query |

  Scenario Outline: Broad scope with no prepared books fails without loading source
    Given the journey book has prepared passage search
    And I ask across the <scope> with an unprepared member book
    And the prepared search is missing windows
    When I ask why the traveller returned home
    Then I am told that an answer is temporarily unavailable
    And no canonical source or provider request is loaded

    Examples:
      | scope   |
      | series  |
      | library |

  Scenario: Preparation lost during a broad query cannot trigger complete source reads
    Given the journey book has prepared passage search
    And I ask across the series with an unprepared member book
    And the prepared search is cleared while embedding the query
    When I ask why the traveller returned home
    Then I am told that an answer is temporarily unavailable
    And no canonical source or answer generation is loaded

  Scenario: Hybrid answering recovers a passage outside dense candidates
    Given the journey book has prepared passage search
    And the journey book is declared English
    And only lexical search can find the supporting passage
    When I ask why the traveller returned home
    Then only selected canonical passages are read and supplied

  Scenario: English retrieval requires explicit English question wording
    Given the journey book has prepared passage search
    And the English wording of the question is missing
    When I ask why the traveller returned home
    Then I am told that an answer is temporarily unavailable
    And no canonical source or provider request is loaded

  Scenario Outline: Unusable query embeddings do not trigger whole-book fallback
    Given the journey book has prepared passage search
    And the answer service encounters <problem>
    When I ask why the traveller returned home
    Then I am told that an answer is temporarily unavailable

    Examples:
      | problem                |
      | an invalid embedding   |
      | an embedding rejection |

  Scenario: Model input limits reduce retrieved evidence without truncating passages
    Given the journey book has prepared passage search
    And the answer model has a small passage budget
    When I ask why the traveller returned home
    Then the answer uses fewer complete passages

  Scenario: Excluded unprepared source does not consume the model budget
    Given the journey book has prepared passage search
    And I ask across the series with an unprepared member book
    And the answer model has a small passage budget
    When I ask why the traveller returned home
    Then the answer uses fewer complete passages
    And the unprepared member source is excluded

  Scenario: A reference outside selected evidence cannot be returned
    Given the journey book has prepared passage search
    And the answer cites a passage outside the selected evidence
    When I ask why the traveller returned home
    Then I am told that an answer is temporarily unavailable

  Scenario: Membership changes invalidate pending series answers
    Given the journey book has prepared passage search
    And I ask across the series with an unprepared member book
    And the journey book leaves its series while the answer is prepared
    When I ask why the traveller returned home
    Then the source change completes and I am told to ask again

  Scenario Outline: An unavailable series never expands to the library
    Given I select a <state> series
    When I ask why the traveller returned home
    Then I am told the selected series is <availability>
    And no canonical source or provider request is loaded

    Examples:
      | state   | availability |
      | empty   | unavailable  |
      | removed | missing      |

  Scenario Outline: Whole-book answering is an explicit choice independent of preparation
    Given the journey book has prepared passage search
    And the prepared search is <state>
    And I explicitly request whole-book answering
    When I ask why the traveller returned home
    Then the complete book is supplied without query embedding or vector search

    Examples:
      | state           |
      | complete        |
      | missing windows |

  Scenario Outline: Explicit whole-book answering never shrinks the source to fit
    Given I explicitly request whole-book answering
    And the complete request exceeds the <limit> input limit
    When I ask why the traveller returned home
    Then I am told the selected evidence is too large without submitting an answer

    Examples:
      | limit      |
      | provider   |
      | configured |

  Scenario: Explicit whole-book source is preflighted before loading text
    Given the journey book has prepared passage search
    And I explicitly request whole-book answering
    And the source read budget is too small
    When I ask why the traveller returned home
    Then the scoped source is reported too large
    And no canonical source or provider request is loaded
