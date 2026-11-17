"""Reading a Catalogue CSV file: each row checked on its own (spec 48-49; issue 13). Pure."""

from __future__ import annotations

import pytest

from catalogue.csv_import import (
    MAX_CSV_ROWS,
    CatalogueFileError,
    CatalogueRow,
    RowProblem,
    parse_catalogue_csv,
)
from catalogue.models import CourseLevel
from catalogue.types import NewCourse
from students.models import Country

HEADER = "university,country,course,level,intakes"
DEAKIN_ROW = 'Deakin University,Australia,Master of Data Science,Master,"Mar 2027;Jul 2027"'


def csv_of(*rows: str, header: str = HEADER) -> str:
    """A file with the header and these rows."""
    return "\n".join((header, *rows)) + "\n"


def problems_of(*rows: str) -> list[RowProblem]:
    """The problems found in a file of these rows."""
    return list(parse_catalogue_csv(csv_of(*rows)).problems)


class TestGoodRows:
    def test_a_row_becomes_a_university_and_a_course(self) -> None:
        parsed = parse_catalogue_csv(csv_of(DEAKIN_ROW))

        assert parsed.rows == (
            CatalogueRow(
                row=2,
                university="Deakin University",
                country=Country.AUSTRALIA,
                course=NewCourse(
                    name="Master of Data Science",
                    level=CourseLevel.MASTER,
                    intakes=("2027-03", "2027-07"),
                ),
            ),
        )
        assert parsed.problems == ()

    def test_rows_are_numbered_as_a_spreadsheet_shows_them(self) -> None:
        parsed = parse_catalogue_csv(csv_of(DEAKIN_ROW, "", DEAKIN_ROW.replace("Master of", "MSc")))

        assert [row.row for row in parsed.rows] == [2, 4]

    def test_columns_can_come_in_any_order_and_any_case(self) -> None:
        text = csv_of(
            'Master of Data Science,"Mar 2027",Deakin University,master,AUSTRALIA',
            header=" Course , Intakes,University,LEVEL,country",
        )

        (row,) = parse_catalogue_csv(text).rows

        assert row.university == "Deakin University"
        assert row.country == Country.AUSTRALIA
        assert row.course == NewCourse(
            name="Master of Data Science", level=CourseLevel.MASTER, intakes=("2027-03",)
        )

    @pytest.mark.parametrize(
        ("written", "level"),
        [
            ("Bachelor", CourseLevel.BACHELOR),
            ("graduate certificate", CourseLevel.GRADUATE_CERTIFICATE),
            ("Graduate  Certificate", CourseLevel.GRADUATE_CERTIFICATE),
            ("graduate_certificate", CourseLevel.GRADUATE_CERTIFICATE),
            ("FOUNDATION", CourseLevel.FOUNDATION),
        ],
    )
    def test_a_level_is_read_by_its_name(self, written: str, level: CourseLevel) -> None:
        (row,) = parse_catalogue_csv(csv_of(f"Deakin,Australia,A course,{written},Mar 2027")).rows

        assert row.course.level == level

    @pytest.mark.parametrize(
        ("written", "country"),
        [
            ("New Zealand", Country.NEW_ZEALAND),
            ("uk", Country.UK),
            ("new_zealand", Country.NEW_ZEALAND),
        ],
    )
    def test_a_country_is_read_by_its_name(self, written: str, country: Country) -> None:
        (row,) = parse_catalogue_csv(csv_of(f"Deakin,{written},A course,Master,Mar 2027")).rows

        assert row.country == country

    @pytest.mark.parametrize(
        "intakes",
        [
            "Mar 2027;Jul 2027",
            "march 2027; JULY 2027",
            "2027-07;2027-03",
            "Jul 2027, Mar 2027",
            "Mar 2027;;Jul 2027;Mar 2027;",
        ],
    )
    def test_intakes_are_read_in_order_each_once(self, intakes: str) -> None:
        (row,) = parse_catalogue_csv(csv_of(f'Deakin,Australia,A course,Master,"{intakes}"')).rows

        assert row.course.intakes == ("2027-03", "2027-07")

    def test_spaces_around_values_are_dropped(self) -> None:
        (row,) = parse_catalogue_csv(
            csv_of("  Deakin University ,Australia,  Master of IT ,Master, Mar 2027 ")
        ).rows

        assert row.university == "Deakin University"
        assert row.course.name == "Master of IT"

    def test_a_byte_order_mark_and_windows_line_endings_are_fine(self) -> None:
        text = "﻿" + csv_of(DEAKIN_ROW).replace("\n", "\r\n")

        parsed = parse_catalogue_csv(text)

        assert len(parsed.rows) == 1
        assert parsed.problems == ()

    def test_injection_shaped_names_are_kept_as_plain_text(self) -> None:
        name = "<script>alert(1)</script>'; DROP TABLE catalogue_university; --"

        (row,) = parse_catalogue_csv(csv_of(f'"{name}",Australia,A course,Master,Mar 2027')).rows

        assert row.university == name


class TestBadRows:
    def test_every_bad_row_is_reported_with_its_number_and_good_rows_are_kept(self) -> None:
        parsed = parse_catalogue_csv(
            csv_of(
                DEAKIN_ROW,
                "Monash University,Australia,Bachelor of IT,Bachelors,Feb 2027",
                ",Australia,Master of IT,Master,Feb 2027",
            )
        )

        assert [row.row for row in parsed.rows] == [2]
        assert parsed.problems == (
            RowProblem(
                3,
                "Level “Bachelors” isn't one of Bachelor, Master, Diploma, "
                "Graduate Certificate, Foundation.",
            ),
            RowProblem(4, "University name is empty."),
        )

    def test_every_problem_in_one_row_is_reported(self) -> None:
        assert problems_of(",Mars,,Bachelors,") == [
            RowProblem(2, "University name is empty."),
            RowProblem(
                2,
                "Country “Mars” isn't one of Australia, UK, Canada, USA, "
                "New Zealand, Japan, Other.",
            ),
            RowProblem(2, "Course name is empty."),
            RowProblem(
                2,
                "Level “Bachelors” isn't one of Bachelor, Master, Diploma, "
                "Graduate Certificate, Foundation.",
            ),
            RowProblem(2, "Add at least one intake."),
        ]

    def test_an_empty_country_or_level_is_named(self) -> None:
        assert problems_of("Deakin,,A course,,Mar 2027") == [
            RowProblem(2, "Country is empty."),
            RowProblem(2, "Level is empty."),
        ]

    @pytest.mark.parametrize("intake", ["Fall 2027", "Sep 27", "1999-09", "2027-13", "Sept 2027"])
    def test_a_badly_written_intake_is_named(self, intake: str) -> None:
        assert problems_of(f'Deakin,Australia,A course,Master,"Mar 2027;{intake}"') == [
            RowProblem(2, f"Intake “{intake}” must look like “Sep 2027”.")
        ]

    def test_too_many_intakes_are_refused(self) -> None:
        intakes = ";".join(
            f"{year}-{month:02d}" for year in (2027, 2028, 2029) for month in (1, 5, 9)
        )
        many = ";".join(f"2027-{month:02d}" for month in range(1, 13))
        more = ";".join(f"2028-{month:02d}" for month in range(1, 13))

        fine = problems_of(f'Deakin,Australia,A course,Master,"{intakes}"')
        too_many = problems_of(f'Deakin,Australia,A course,Master,"{many};{more};2029-01"')

        assert fine == []
        assert too_many == [RowProblem(2, "A course can have at most 24 intakes.")]

    def test_over_long_names_are_refused(self) -> None:
        long_name = "x" * 201

        assert problems_of(f"{long_name},Australia,{long_name},Master,Mar 2027") == [
            RowProblem(2, "University name is longer than 200 characters."),
            RowProblem(2, "Course name is longer than 200 characters."),
        ]

    def test_names_with_control_characters_are_refused(self) -> None:
        assert problems_of('"Deakin\nUniversity",Australia,"A\tcourse",Master,Mar 2027') == [
            RowProblem(2, "University name has characters that aren't allowed."),
            RowProblem(2, "Course name has characters that aren't allowed."),
        ]

    @pytest.mark.parametrize(
        ("row", "count"),
        [
            ("Deakin,Australia,A course,Master", 4),
            ("Deakin,Australia,A course,Master,Mar 2027,x", 6),
        ],
    )
    def test_a_row_with_the_wrong_number_of_columns_is_refused(self, row: str, count: int) -> None:
        assert problems_of(row) == [RowProblem(2, f"This row has {count} columns; it needs 5.")]

    def test_a_long_bad_value_is_shortened_in_the_message(self) -> None:
        (problem,) = problems_of(f"Deakin,{'A' * 100},A course,Master,Mar 2027")

        assert problem.problem.startswith("Country “" + "A" * 40 + "…” isn't one of")

    def test_control_characters_are_left_out_of_messages(self) -> None:
        (problem,) = problems_of('Deakin,"Mars\x1b[31m",A course,Master,Mar 2027')

        assert problem.problem.startswith("Country “Mars[31m” isn't one of")


class TestBadFiles:
    @pytest.mark.parametrize("text", ["", "   \n\n", "﻿"])
    def test_an_empty_file_is_refused(self, text: str) -> None:
        with pytest.raises(CatalogueFileError, match=r"The file is empty\."):
            parse_catalogue_csv(text)

    @pytest.mark.parametrize(
        "header",
        [
            "university,country,course,level",
            "university,country,course,level,intakes,notes",
            "university,country,course,level,level",
            "Deakin University,Australia,Master of Data Science,Master,Mar 2027",
        ],
    )
    def test_a_file_without_the_column_names_is_refused(self, header: str) -> None:
        with pytest.raises(CatalogueFileError, match="The first row must name the columns"):
            parse_catalogue_csv(csv_of(DEAKIN_ROW, header=header))

    def test_blank_lines_before_the_column_names_are_skipped(self) -> None:
        parsed = parse_catalogue_csv("\n,,,,\n" + csv_of(DEAKIN_ROW))

        assert [row.row for row in parsed.rows] == [4]

    def test_a_file_with_only_the_column_names_has_no_rows(self) -> None:
        parsed = parse_catalogue_csv(csv_of())

        assert parsed.rows == ()
        assert parsed.problems == ()

    def test_a_file_with_too_many_rows_is_refused(self) -> None:
        fine = parse_catalogue_csv(csv_of(*[DEAKIN_ROW] * MAX_CSV_ROWS))

        assert len(fine.rows) == MAX_CSV_ROWS
        with pytest.raises(CatalogueFileError, match="at most 2,000 rows"):
            parse_catalogue_csv(csv_of(*[DEAKIN_ROW] * (MAX_CSV_ROWS + 1)))

    @pytest.mark.parametrize(
        "text", [csv_of('"Deakin,Australia,A course,Master,Mar 2027'), csv_of("Dea\x00kin,a,b,c,d")]
    )
    def test_a_file_that_isnt_csv_is_refused(self, text: str) -> None:
        with pytest.raises(CatalogueFileError, match=r"We couldn't read this file as CSV\."):
            parse_catalogue_csv(text)
