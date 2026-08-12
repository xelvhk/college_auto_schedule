import fs from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const outputDir = new URL(
  "../outputs/019ff10a-3626-7ba3-81dc-2686348726da/",
  import.meta.url,
);
const fixtureDir = new URL("../tests/fixtures/", import.meta.url);

const workbook = Workbook.create();
const sheets = [
  {
    name: "Преподаватели",
    headers: [
      "teacher_code",
      "full_name",
      "department",
      "employment_type",
      "yearly_assigned_hours",
      "yearly_limit_hours",
      "max_hours_per_day",
      "max_days_per_week",
      "home_building_code",
      "active",
    ],
    example: [
      "T-001",
      "Иванова Ирина Игоревна",
      "Информационные технологии",
      "staff",
      720,
      900,
      8,
      5,
      "MAIN",
      true,
    ],
  },
  {
    name: "Группы",
    headers: [
      "group_code",
      "specialty_code",
      "curriculum_code",
      "course",
      "education_form",
      "headcount",
      "program_base",
      "study_week_type",
      "primary_building_code",
      "subgroup_count",
    ],
    example: [
      "ИС-101",
      "09.02.07",
      "UP-09.02.07-2026",
      1,
      "full_time",
      25,
      "9_classes",
      "six_days",
      "MAIN",
      1,
    ],
  },
  {
    name: "Специальности",
    headers: [
      "specialty_code",
      "specialty_name",
      "qualification",
      "program_base",
      "education_form",
      "active",
    ],
    example: [
      "09.02.07",
      "Информационные системы и программирование",
      "Специалист по информационным системам",
      "9",
      "full_time",
      true,
    ],
  },
  {
    name: "Учебные планы",
    headers: [
      "curriculum_code",
      "specialty_code",
      "admission_year",
      "version",
      "valid_from",
      "valid_to",
      "status",
    ],
    example: [
      "UP-09.02.07-2026",
      "09.02.07",
      2026,
      "1.0",
      new Date("2026-09-01T00:00:00Z"),
      null,
      "active",
    ],
  },
  {
    name: "Дисциплины",
    headers: [
      "curriculum_code",
      "discipline_code",
      "discipline_name",
      "section_code",
      "semester",
      "lesson_type",
      "planned_hours",
      "control_form",
    ],
    example: [
      "UP-09.02.07-2026",
      "MDK.01.01",
      "Основы программирования",
      "ПМ.01",
      1,
      "practice",
      72,
      "credit",
    ],
  },
  {
    name: "Студенты",
    headers: [
      "student_code",
      "full_name",
      "group_code",
      "status",
      "enrollment_date",
      "end_date",
      "subgroup_codes",
      "elective_codes",
    ],
    example: [
      "S-001",
      "Петров Пётр Петрович",
      "ИС-101",
      "active",
      new Date("2026-09-01T00:00:00Z"),
      null,
      "1",
      "WEB",
    ],
  },
  {
    name: "Корпуса",
    headers: ["building_code", "building_name", "active"],
    example: ["MAIN", "Главный корпус", true],
  },
  {
    name: "Типы помещений",
    headers: ["room_type_code", "room_type_name", "active"],
    example: ["COMPUTER_LAB", "Компьютерный класс", true],
  },
  {
    name: "Оборудование",
    headers: ["equipment_code", "equipment_name", "active"],
    example: ["COMPUTERS", "Компьютеры", true],
  },
  {
    name: "Аудитории",
    headers: [
      "room_code",
      "room_name",
      "building_code",
      "room_type_code",
      "capacity",
      "equipment_codes",
      "active",
    ],
    example: [
      "MAIN-201",
      "Лаборатория 201",
      "MAIN",
      "COMPUTER_LAB",
      25,
      "COMPUTERS",
      true,
    ],
  },
  {
    name: "Нагрузка",
    headers: [
      "workload_row_code",
      "academic_year",
      "semester",
      "discipline_code",
      "discipline_name",
      "group_code",
      "subgroup",
      "stream",
      "teacher_code",
      "lesson_type",
      "total_academic_hours",
      "event_duration_hours",
      "recurrence",
      "lesson_bundle_code",
      "room_type",
      "room_capacity",
      "required_equipment_codes",
    ],
    example: [
      "W-001",
      "2026/2027",
      1,
      "MDK.01.01",
      "Основы программирования",
      "ИС-101",
      null,
      null,
      "T-001",
      "practice",
      72,
      2,
      "weekly",
      null,
      "computer_lab",
      25,
      "COMPUTERS",
    ],
  },
];

for (const definition of sheets) {
  const sheet = workbook.worksheets.add(definition.name);
  const lastColumn = String.fromCharCode(64 + definition.headers.length);
  sheet.getRange(`A1:${lastColumn}2`).values = [
    definition.headers,
    definition.example,
  ];
  sheet.getRange(`A1:${lastColumn}1`).format = {
    fill: "#1F4E78",
    font: { bold: true, color: "#FFFFFF" },
    wrapText: true,
    verticalAlignment: "center",
  };
  sheet.getRange(`A1:${lastColumn}2`).format.borders = {
    preset: "all",
    style: "thin",
    color: "#B4C6E7",
  };
  sheet.getRange(`A1:${lastColumn}2`).format.autofitColumns();
  sheet.getRange(`A1:${lastColumn}1`).format.rowHeight = 36;
  sheet.showGridLines = false;
  sheet.freezePanes.freezeRows(1);
}

workbook.worksheets
  .getItem("Учебные планы")
  .getRange("E2:F2")
  .setNumberFormat("yyyy-mm-dd");
workbook.worksheets
  .getItem("Студенты")
  .getRange("E2:F2")
  .setNumberFormat("yyyy-mm-dd");

await fs.mkdir(outputDir, { recursive: true });
await fs.mkdir(fixtureDir, { recursive: true });
const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save(
  fileURLToPath(new URL("college-import-template.xlsx", outputDir)),
);
await output.save(fileURLToPath(new URL("valid-import.xlsx", fixtureDir)));

// Негативная фикстура подтверждает, что импорт не принимает вычисляемые ячейки.
workbook.worksheets.getItem("Преподаватели").getRange("J2").formulas = [
  ["=TRUE()"],
];
const formulaFixture = await SpreadsheetFile.exportXlsx(workbook);
await formulaFixture.save(
  fileURLToPath(new URL("formula-import.xlsx", fixtureDir)),
);
workbook.worksheets.getItem("Преподаватели").getRange("J2").values = [[true]];

for (const definition of sheets) {
  const preview = await workbook.render({
    sheetName: definition.name,
    autoCrop: "all",
    scale: 1,
    format: "png",
  });
  await fs.writeFile(
    new URL(`${definition.name}.png`, outputDir),
    new Uint8Array(await preview.arrayBuffer()),
  );
}

const inspection = await workbook.inspect({
  kind: "sheet,formula",
  maxChars: 4000,
  options: { maxResults: 100 },
});
console.log(inspection.ndjson);
